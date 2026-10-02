<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Musicer Agent Guide

本文件是 AI 编码代理在仓库中的入口地图。只记录稳定规则、模块入口和文档路由；具体业务设计按需读取对应文档，不要把所有上下文一次性加载进来。

## 项目定位

Musicer 是一个面向个人曲库的智能音乐播放器。Next.js 前端负责播放和交互，FastAPI 后端提供媒体、配置、记忆、知识库和 API，Qwen 驱动的 LangGraph ReAct Agent 通过工具与 Skill 完成搜索、下载、播放控制和知识处理。

当前实现与规划边界以 [README.md](README.md)、[docs/项目架构说明.md](docs/项目架构说明.md) 和 [docs/需求文档.md](docs/需求文档.md) 为准。不要把需求文档中的规划能力描述成已经上线。

## 先按任务定位入口

| 任务 | 首要入口 | 按需继续读取 |
| --- | --- | --- |
| 前端页面、播放器、聊天 UI | `frontend/app/page.tsx`、`frontend/app/context/` | `frontend/app/components/`、`frontend/app/hooks/`、`frontend/app/api/` |
| 后端启动、路由和生命周期 | `backend/main.py` | `backend/routers/`、`backend/models.py` |
| ReAct Agent、工具、上下文 | `backend/services/ai_agent.py` | `backend/services/llm_client.py`、`backend/routers/chat.py` |
| 记忆与 Dream | `backend/services/memory_store.py`、`backend/services/memory_manager.py` | `backend/services/episode_memory.py`、`backend/services/dream_engine.py`、`docs/记忆系统说明.md` |
| LLM-Wiki | `skills/llm-wiki/SKILL.md` | `skills/llm-wiki/references/`、`skills/llm-wiki/scripts/`、`backend/services/wiki_*.py` |
| 本地/B站搜索与下载 | `backend/services/music_manager.py`、`backend/services/bili_client.py` | `skills/local-search/`、`skills/cloud-search/`、`skills/convert/` |
| 播放会话、状态与动作回执 | `frontend/app/context/PlayerContext.tsx`、`frontend/app/context/AgentContext.tsx` | `backend/services/playback_session_store.py`、`backend/services/player_action_store.py`、`backend/routers/playback.py` |
| 本地曲库、最近播放、命名歌单 | `frontend/app/components/organisms/MusicWorkspace.tsx` | `frontend/app/context/PlaylistContext.tsx`、`backend/services/music_library_store.py`、`backend/routers/music_library.py` |
| 对话推荐、Radio 与反馈 | `skills/music-recommendation/SKILL.md`、`backend/services/recommendation_service.py` | `backend/services/preference_service.py`、`backend/services/music_library_store.py`、`frontend/app/context/PlayerContext.tsx` |
| 场景共享状态 | `frontend/app/context/ScenarioContext.tsx` | `frontend/app/context/AgentContext.tsx`、`frontend/app/context/PlayerContext.tsx` |
| 语音 | `frontend/app/hooks/useVoiceRecorder.ts` | `backend/routers/voice.py`、`backend/services/voice_service.py` |
| Docker 安装与运行 | `install.md`、`docker-compose.yml` | `backend/Dockerfile`、`frontend/Dockerfile` |
| 测试 | `backend/tests/` | 与被修改服务同名的 `test_*.py` |

## 文档索引

- [README.md](README.md)：面向开发者的项目概览、稳定能力、模块入口和快速开始。
- [install.md](install.md)：唯一正式安装路径，使用 Docker Compose。
- [docs/项目架构说明.md](docs/项目架构说明.md)：当前运行架构、模块职责、数据流、已知缺口和目标演进。
- [docs/需求文档.md](docs/需求文档.md)：产品目标、功能需求、优先级和验收标准。
- [docs/记忆系统说明.md](docs/记忆系统说明.md)：当前记忆 v2.2 的数据层、长期记忆/episode 召回、场景投影、触发策略和边界。
- [docs/references/README.md](docs/references/README.md)：外部 API 参考资料索引，只作为实现依据，不作为项目现状说明。
- `skills/*/SKILL.md`：运行时 Agent Skill 的真实说明；修改 Skill 行为时以这些文件为入口。

## 开发规则

- 修改前先读取最小必要入口；只有入口指向其他文件时再继续加载。
- 保持分层：路由只做协议和校验，业务逻辑进入 `backend/services/`，UI 状态进入 Context/Hook，可复用展示进入组件。
- Agent 的能力由已注册工具和 Skill 决定。不要在系统提示词中重复完整工具清单，也不要写死可由 ReAct 自主决定的调用顺序。
- Skill 统一放在根目录 `skills/<name>/`。`SKILL.md` 描述工作流；确定性操作放在 `scripts/`；详细材料放在 `references/`。
- LLM-Wiki 写入、重置、网络取证遵守 `skills/llm-wiki/SKILL.md`；本地音频与 Wiki 数据是不同生命周期，重置 Wiki 不得删除歌曲。
- `memory/`、`LLM-Wiki/`、`db/` 和音乐目录含运行时或用户数据。测试使用隔离目录，不要清空真实数据。
- 环境变量中的密钥不得写入代码、文档、镜像或提交；只提交 `.env.example`。
- 业务文档使用中文文件名与中文正文；行业约定文件保留英文名，例如 `README.md`、`AGENTS.md`、`install.md`、`Dockerfile`。
- 行为变更必须同步更新其唯一事实来源，避免在 README、架构文档和代码注释中复制同一份细节。

## 验证入口

```powershell
# 后端测试
python -m pytest backend/tests

# 前端构建
npm run build

# Docker 配置与构建
docker compose config --quiet
docker compose build
```

按改动范围选择最小验证集；跨前后端或安装链路的改动再执行完整验证。Docker 的启动、日志和数据卷说明见 [install.md](install.md)。
