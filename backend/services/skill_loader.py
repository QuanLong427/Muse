"""
Skill Loader — Agent Skills 标准实现

遵循 Agent Skills 规范（agentskills.io/specification）：
- discover_skills(): 扫描目录，读取 frontmatter
- load_skill(): 按需加载完整 SKILL.md 正文
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SKILL_FILENAME = "SKILL.md"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SKILLS_LIBRARY = PROJECT_ROOT / "skills"

# YAML frontmatter pattern: --- ... ---
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(raw: str) -> Dict[str, str]:
    """Parse YAML frontmatter from SKILL.md content."""
    meta: Dict[str, str] = {}
    m = FRONTMATTER_PATTERN.match(raw)
    if not m:
        return meta
    for line in m.group(1).splitlines():
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def _skill_roots(skills_root: Optional[Path] = None) -> List[Path]:
    if skills_root is not None:
        return [Path(skills_root)]
    return [DEFAULT_SKILLS_LIBRARY]


def discover_skills(skills_root: Optional[Path] = None) -> List[Dict[str, str]]:
    """
    发现技能：扫描技能库目录，读取每个子目录中的 SKILL.md 的 frontmatter（name、description）。
    符合 Agent Skills 的 Progressive disclosure：仅加载元数据（约 100 tokens/Skill）。

    Args:
        skills_root: 技能库根目录，默认 DEFAULT_SKILLS_LIBRARY。

    Returns:
        列表，每项为 {"name": str, "description": str, ...}，按 name 排序。
    """
    result: List[Dict[str, str]] = []
    seen_names = set()
    for root in _skill_roots(skills_root):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_dir():
                continue
            skill_md = path / SKILL_FILENAME
            if not skill_md.is_file():
                continue
            try:
                raw = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = _parse_frontmatter(raw)
            name = meta.get("name")
            if name and name not in seen_names:
                result.append(meta)
                seen_names.add(name)
    return sorted(result, key=lambda item: item["name"])


def load_skill(skill_name: str, skills_root: Optional[Path] = None) -> Tuple[str, str]:
    """
    加载技能：读取指定技能目录下的完整 SKILL.md 内容（正文 + 可选 frontmatter）。
    仅在「选择」该技能后调用，符合按需加载。

    Args:
        skill_name: 技能名称，对应子目录名（如 local-search、cloud-search）。
        skills_root: 技能库根目录，默认 DEFAULT_SKILLS_LIBRARY。

    Returns:
        (full_content, body_only)。full_content 为完整文件内容；body_only 为去掉 frontmatter 的正文。
    """
    full = ""
    for root in _skill_roots(skills_root):
        if not root.is_dir():
            continue
        for skill_dir in root.iterdir():
            skill_md = skill_dir / SKILL_FILENAME
            if not skill_dir.is_dir() or not skill_md.is_file():
                continue
            try:
                candidate = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _parse_frontmatter(candidate).get("name") == skill_name:
                full = candidate
                break
        if full:
            break
    if not full:
        return "", ""

    # 去掉 frontmatter 得到正文（供 LLM 使用）
    body = full
    m = FRONTMATTER_PATTERN.match(full)
    if m:
        body = full[m.end():].strip()
    return full, body


def find_skill_directory(
    skill_name: str, skills_root: Optional[Path] = None
) -> Optional[Path]:
    """Resolve a discovered Skill directory by frontmatter name."""
    for root in _skill_roots(skills_root):
        if not root.is_dir():
            continue
        for skill_dir in root.iterdir():
            skill_md = skill_dir / SKILL_FILENAME
            if not skill_dir.is_dir() or not skill_md.is_file():
                continue
            try:
                raw = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _parse_frontmatter(raw).get("name") == skill_name:
                return skill_dir.resolve()
    return None


def load_skill_resource(
    skill_name: str,
    resource_path: str,
    skills_root: Optional[Path] = None,
) -> str:
    """Read a text resource confined to one Skill's references directory."""
    skill_dir = find_skill_directory(skill_name, skills_root)
    if skill_dir is None:
        raise ValueError("unknown_skill")
    references_root = (skill_dir / "references").resolve()
    candidate = (references_root / resource_path).resolve()
    try:
        candidate.relative_to(references_root)
    except ValueError as exc:
        raise ValueError("invalid_skill_resource") from exc
    if candidate.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml"}:
        raise ValueError("unsupported_skill_resource")
    if not candidate.is_file():
        raise ValueError("skill_resource_not_found")
    return candidate.read_text(encoding="utf-8", errors="replace")
