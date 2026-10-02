---
name: cloud-search
description: 通过 B站 API 搜索明确的云端视频资源；用于用户指定云端来源或本地精确查找无结果后的补充，不负责个性化推荐编排
---
## 云端搜索 Skill

通过 B站 搜索资源。B站 拥有各类视频资源（音乐、科普、课程、演讲、访谈、纪录片等），本应用会将视频转为音频供用户收听。

### 使用前提（重要）

**仅在以下情况使用本 Skill：**
1. `local_search` 返回 total=0（本地没有该资源）
2. 用户明确说"去B站搜"、"云端搜索"、"网上找"

**禁止在未调用 local_search 的情况下直接使用本 Skill。**

例外：推荐意图由 `music-recommendation` Skill 和 `recommend_music` 统一编排，
它可以按用户指定来源或默认本地/云端配额直接使用云端检索能力。

### 搜索步骤

1. 解析用户意图，提取搜索关键词
2. 使用 `bili_search` 工具搜索B站视频（直接传入中文关键词即可）：
   bili_search(keyword="关键词")
   返回 JSON: { "total": number, "videos": [{ "bvid", "title", "author", "duration", "play", "pic" }] }
3. 分析搜索结果，筛选最相关的视频（通常 5-10 个）
4. 调用 `present_tracks(track_ids=[...])`，传入选中结果的精确 BVID；后端据此显示真实 Track 卡片

### 输出约束

- 不要把 `bili_search` 写成 Bash、代码块或命令示例，必须发出真实工具调用
- 不要重新生成搜索结果 JSON；Track 卡片由后端从工具结果构造
- 只能把本轮 `bili_search` 返回的视频描述成在线候选，不得自行补写 BVID、UP 主或可下载状态
- `Hi-Res`、`无损`、`原唱`等只能描述为视频标题中的来源方声明，未核验时不能直接断言

### 搜索后的下一步

搜索到结果后展示下载动作，并提示用户点击卡片的 DOWNLOAD 确认具体版本。只有客户端提交结构化 `selected_tracks` 后，才调用 `convert_video` 将视频转为音频并登记到本地曲库；普通文本确认不构成下载授权。
