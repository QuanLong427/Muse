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

- Agent 提示环境变量缺失：确认已经创建 `.env` 和 `backend/.env.local`，并重新创建后端容器。
- 前端能打开但没有歌曲：确认 `MUSIC_PATH` 是宿主机真实目录，并允许 Docker Desktop 访问该磁盘。
- Agent 提示 API 未配置：检查 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`，然后执行 `docker compose up -d --force-recreate backend`。
- B 站返回 `412 request was banned`：这通常是当前出口 IP、请求频率或登录状态触发风控，不等于视频设置了访问限制。若正在使用 VPN，先关闭 VPN，或让 `*.bilibili.com`、`*.bilivideo.com`、`*.hdslb.com` 走直连；必要时再配置上述 Cookie。不要连续重试。
- B 站转换的其他错误：查看 `docker compose logs backend`；镜像已包含 yt-dlp、浏览器模拟依赖和 ffmpeg，不需要在宿主机单独安装。
- 修改前端后看不到变化：执行 `docker compose up -d --build frontend`。
