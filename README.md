[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Install-Docker_Compose-2496ED)](install.md)

# Musicer

Musicer 是一个由 AI Agent 驱动的个人音乐播放器：统一管理本地音乐与 B 站音频来源，通过自然语言完成检索、下载、播放控制、偏好记忆和音乐知识整理。

![Musicer 主界面](./assets/image-20260610105525929.png)

## 当前稳定能力

- Next.js 16 播放器：本地 MP3 扫描、Range 播放、队列、进度、音量和弹幕。
- LangGraph ReAct Agent：Qwen3.5-Flash 多轮对话、SSE 流式输出、工具自主决策和浏览器播放器控制。
- 本地与云端搜索：优先检索本地曲库，必要时搜索 B 站并在用户确认后转换为 MP3。
- 语音交互：Qwen ASR 转写后进入同一 Agent，可选 Qwen TTS 播报回答。
- 记忆 v2.1：SQLite 保存原始事件、轮次 episode、Dream 候选和可审计的长期偏好，并在每轮推理前条件召回相关 episode。
- LLM-Wiki Skill：渐进加载知识库工作流，通过受限脚本执行查询、预览入库、审计和重置。
- 联网取证：为翻唱、Live、Remix 等易混淆版本搜索并抓取可核验来源。
- Docker Compose：统一启动前端、后端并挂载音乐、记忆和 Wiki 数据。

命名歌单、正式播放模式、推荐反馈闭环、向量语义召回和对话压缩仍属于规划能力，不能按已完成能力使用。范围和优先级见[需求文档](./docs/需求文档.md)。

## 稳定架构

```text
Browser
  ├─ Next.js UI / Player / API routes
  └─ SSE chat + player actions
             │
             ▼
FastAPI routers
  ├─ LangGraph ReAct Agent ── tools / skills / Qwen
  ├─ local music / Bilibili / voice / web evidence
  ├─ memory v2 / playlist SQLite
  └─ LLM-Wiki domain services
             │
             ▼
mounted music + memory/ + db/ + LLM-Wiki/
```

当前实现、调用链、数据边界和规划演进统一记录在[项目架构说明](./docs/项目架构说明.md)。

## 主要模块

| 模块 | 职责 | 入口 |
| --- | --- | --- |
| `frontend/` | 播放器、聊天、语音、弹幕和 Next.js API 路由 | `frontend/app/page.tsx` |
| `backend/` | FastAPI、Agent、领域服务、持久化和外部接口 | `backend/main.py` |
| `skills/` | Agent 可渐进加载的工作流、参考和受限脚本 | `skills/*/SKILL.md` |
| `memory/` | 会话、长期记忆和播放队列运行时数据 | `memory/data/` |
| `LLM-Wiki/` | 运行时生成的音乐知识库 | `skills/llm-wiki/SKILL.md` |
| `docs/` | 需求、架构和外部参考 | `docs/项目架构说明.md` |

技术栈包括 Next.js 16、React 19、TypeScript、FastAPI、LangGraph、SQLite、Qwen OpenAI-compatible API、yt-dlp 和 ffmpeg。

## 快速开始

项目以 Docker Compose 作为正式安装方式。准备 Docker Desktop 或 Docker Engine + Compose 后：

```bash
docker compose up -d --build
```

首次运行前必须配置音乐目录和 Qwen API 凭据。完整步骤、Windows 路径写法、数据持久化和故障排查见 [install.md](./install.md)。启动后访问 <http://localhost:3000>，后端健康检查为 <http://localhost:8000/health>。

## 文档

- [安装与运行](./install.md)
- [项目架构说明](./docs/项目架构说明.md)
- [需求文档](./docs/需求文档.md)
- [记忆系统说明](./docs/记忆系统说明.md)
- [AI 编码代理入口](./AGENTS.md)

## License

本项目仅供自用或学习参考，采用 [MIT License](./LICENSE)。请仅下载你有权保存和使用的内容，并遵守内容平台的服务条款与当地法律。
