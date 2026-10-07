# Musicer 安装与运行

Musicer 以 Docker Compose 作为正式安装方式。前端、后端、Python 依赖、yt-dlp 和 ffmpeg 均由镜像管理；宿主机只需要 Docker 和一个可读写的音乐目录。

## 1. 前置条件

- Docker Desktop，或 Docker Engine + Docker Compose v2.24 以上。
- 一个阿里云百炼 API Key，以及与该 Key 同业务空间、同地域的 OpenAI-compatible Base URL。
- 一个用于保存 MP3 的宿主机目录。

## 2. 准备配置

在项目根目录执行：

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env.local
```

Linux/macOS：

```bash
cp .env.example .env
cp backend/.env.example backend/.env.local
```

编辑根目录 `.env`：

```env
# 宿主机音乐目录。Windows 推荐正斜杠，例如 D:/Music。
MUSIC_PATH=D:/Music
FRONTEND_PORT=3000
BACKEND_PORT=8000
```

编辑 `backend/.env.local`：

```env
OPENAI_BASE_URL=https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
OPENAI_API_KEY=your-dashscope-api-key-here
MODEL_NAME=qwen3.5-flash

QWEN_AGENT_ENABLE_THINKING=true
QWEN_AGENT_THINKING_BUDGET=2048

VOICE_ASR_MODEL=qwen3-asr-flash
VOICE_TTS_MODEL=qwen-audio-3.0-tts-flash
VOICE_TTS_VOICE=longanhuan_v3.6
```

Base URL 不要包含 `/chat/completions`。TTS 默认模型要求北京地域，API Key、业务空间和 Base URL 必须匹配。`.env` 与 `.env.local` 已被 Git 忽略，不要提交真实密钥。

### 可选：配置 B 站 Cookie

普通公开视频优先匿名下载。如果 B 站返回 `412 request was banned` 或内容要求登录，可将浏览器导出的 Netscape 格式 Cookie 保存为：

```text
secrets/bilibili-cookies.txt
```

Compose 会把 `secrets/` 只读挂载到后端；目录内容已被 Git 忽略。Cookie 属于登录凭据，不要发送给他人或提交到仓库。修改 Cookie 后无需重建镜像，只需重新发起下载。

## 3. 构建并启动

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

访问：

- Web：<http://localhost:3000>
- 后端健康检查：<http://localhost:8000/health>
- FastAPI 文档：<http://localhost:8000/docs>

若修改了 `FRONTEND_PORT` 或 `BACKEND_PORT`，使用对应端口访问。容器内部端口保持不变。

## 4. 数据持久化

Compose 将下列宿主机目录挂载到容器：

| 宿主机 | 用途 | 容器路径 |
| --- | --- | --- |
| `${MUSIC_PATH}` | MP3 曲库和新下载音频 | `/music` |
| `./memory/data` | 会话、长期记忆、播放队列 SQLite | `/app/memory/data` |
| `./LLM-Wiki` | 音乐知识库 | `/app/LLM-Wiki` |
| `./db` | 场景配置和 Wiki 重置清单 | `/app/db` |
| `./secrets` | 可选的 B 站 Cookie，只读 | `/run/secrets/musicer` |

`docker compose down` 不会删除这些目录。重建镜像也不会删除用户数据。不要使用 `down -v` 作为重置 Wiki 或记忆的方式；使用应用提供的显式重置入口。

### SQLite 日志兼容与迁移

默认 `SQLITE_JOURNAL_MODE=DELETE` 保留事务保护，不依赖 WAL 共享内存。Windows Docker 共享挂载建议保持此默认值；仅在兼容的本机文件系统上显式设置 `WAL`。后端健康检查同时验证记忆表可读。

如果旧数据库处于 WAL 模式，并出现容器 `disk I/O error`、宿主机仍可读取，先停止后端及所有数据库查看器，再从宿主机执行离线迁移。脚本先用 SQLite backup API 备份全部目标并验证完整性，再 checkpoint、切换日志模式及核对表记录数。不要手动删除 `-wal`、`-shm` 或原数据库。

```powershell
docker compose stop backend
python backend/scripts/migrate_sqlite_journal.py --offline --database memory/data/memory.db --database memory/data/download-jobs.db --database db/wiki-sync.db --backup-dir memory/data/sqlite-backups/your-unique-backup-name
docker compose up -d --build
```

只列出实际存在的数据库；备份目录必须不存在，重复执行应换新名称。日志模式切换不重置歌曲、歌单或长期记忆。SQLite WAL 的共享内存要求见 [官方说明](https://www.sqlite.org/wal.html)。

迁移时出现 `database is locked` 或 Windows 文件占用，应关闭 SQLiteStudio 等数据库查看器，不能强制删除 sidecar 或自动结束用户进程。Windows 可用只读脚本查询占用者：

```powershell
backend/scripts/find_sqlite_lock_owners.ps1 -DatabasePaths "$PWD/memory/data/memory.db"
```

仅在无法原位切换且已确认所有应用停止时，可使用迁移脚本的 `--restore-snapshot <已验证备份>` 与 `--archive-dir <新归档目录>` 显式恢复单个数据库；恢复前校验完整性、表记录数和逻辑内容摘要，原库及 sidecar 一起归档，不删除原数据。

## 5. 常用运维命令

```bash
# 查看状态和日志
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend

# 重启
docker compose restart

# 更新代码后重建
docker compose up -d --build

# 停止
docker compose down
```

验证容器中的 LLM-Wiki Skill 脚本：

```bash
docker compose exec backend python /app/skills/llm-wiki/scripts/wiki_ops.py status
docker compose exec backend python /app/skills/llm-wiki/scripts/wiki_ops.py audit
```

这些命令只读。入库和重置规则以 `skills/llm-wiki/SKILL.md` 为准。

## 6. 故障排查

### B站下载与 VPN 共存

在 `backend/.env.local` 设置 `BILIBILI_NETWORK_MODE=auto`（默认）。下载和搜索先显式直连，失败后尝试 `BILIBILI_PROXY_URL`；不受模型 API 使用的代理设置影响。`direct` 只直连，`proxy` 只使用代理。没有专用代理时，`auto` 可以使用环境中的 HTTP(S) 代理作为备用。

Docker Desktop 访问宿主机代理的示例：`BILIBILI_PROXY_URL=http://host.docker.internal:7890`。端口按代理客户端实际 HTTP/混合端口填写，客户端需要允许局域网访问；不要使用容器内的 `127.0.0.1`。修改后执行 `docker compose up -d --force-recreate backend`。

显式直连可以绕过应用层 HTTP 代理，但不能绕过 VPN 的全局/TUN 路由。保留 VPN 开启时，在代理客户端为 `bilibili.com`、`bilivideo.com`、`hdslb.com` 及其子域名设置 DIRECT。若希望经过 VPN 下载，使用 `proxy` 和能够访问 B站的出口；B站仍可能限制部分出口 IP 或要求有效 Cookie，软件无法保证所有节点可用。Cookie 配置见前文。

- Agent 提示环境变量缺失：确认已经创建 `.env` 和 `backend/.env.local`，并重新创建后端容器。
- 前端能打开但没有歌曲：确认 `MUSIC_PATH` 是宿主机真实目录，并允许 Docker Desktop 访问该磁盘。
- Agent 提示 API 未配置：检查 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`，然后执行 `docker compose up -d --force-recreate backend`。
- B 站返回 `412 request was banned`：这是出口 IP、请求频率或登录状态触发风控的线索，不等于视频设置了访问限制。检查返回的 `network_attempts`、VPN 分流和 Cookie；不要连续重试同一出口。
- B 站转换的其他错误：查看 `docker compose logs backend`；镜像已包含 yt-dlp、浏览器模拟依赖和 ffmpeg，不需要在宿主机单独安装。
- 修改前端后看不到变化：执行 `docker compose up -d --build frontend`。
