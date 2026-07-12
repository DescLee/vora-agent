from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from vora.models import TaskState


@dataclass(frozen=True)
class TaskStrategy:
    name: str
    description: str
    principles: tuple[str, ...]
    first_evidence: tuple[str, ...]
    risk_order: tuple[str, ...]

    def to_metadata(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "principles": list(self.principles),
            "first_evidence": list(self.first_evidence),
            "risk_order": list(self.risk_order),
        }


def select_task_strategy(task: TaskState) -> TaskStrategy | None:
    profile = detect_project_profile(task.cwd)
    normalized_goal = task.goal.lower()
    if _is_security_goal(normalized_goal):
        return _security_review_strategy(profile)
    if _is_release_goal(normalized_goal):
        return _release_review_strategy(profile)
    if _is_bug_goal(normalized_goal):
        return _bug_strategy(profile)
    if _is_architecture_goal(normalized_goal):
        return _architecture_strategy(profile)
    if _is_code_review_task(task):
        return _code_review_strategy(profile)
    return None


def detect_project_profile(cwd: Path) -> dict:
    package_json = cwd / "package.json"
    profile = {
        "kind": "generic",
        "package_manager": "",
        "scripts": [],
        "has_git": (cwd / ".git").exists(),
        "has_env_files": any(cwd.glob(".env*")),
    }
    if package_json.is_file():
        profile["kind"] = "frontend" if _looks_like_frontend_package(package_json) else "node"
        profile["package_manager"] = _detect_package_manager(cwd)
        profile["scripts"] = _package_scripts(package_json)
        return profile
    if (cwd / "pyproject.toml").is_file():
        profile["kind"] = "python"
        return profile
    if (cwd / "go.mod").is_file():
        profile["kind"] = "go"
        return profile
    if (cwd / "Cargo.toml").is_file():
        profile["kind"] = "rust"
        return profile
    return profile


def _code_review_strategy(profile: dict) -> TaskStrategy:
    if profile.get("kind") in {"frontend", "node"}:
        package_manager = profile.get("package_manager") or "npm/pnpm/yarn"
        scripts = set(profile.get("scripts") or [])
        validation = [
            f"{package_manager} lint" if "lint" in scripts else "可用 lint 脚本",
            _first_matching_script(scripts, ("build:UAT", "build:uat", "build", "typecheck")),
            _first_matching_script(scripts, ("test", "test:unit")),
        ]
        return TaskStrategy(
            name="frontend_code_review",
            description="前端项目审查：验证优先、风险优先，再精读证据文件。",
            principles=(
                "证据优先：先用只读命令获得事实，再读取具体文件。",
                "验证优先：优先运行已有 lint、build、test/typecheck 脚本；没有脚本时说明缺口。",
                "风险优先：发布阻塞和安全问题排在代码风格问题前。",
                "输出克制：只列可验证、高影响的问题，低价值清理项放后或省略。",
            ),
            first_evidence=(
                "git status --short 和 git diff --stat，确认工作区状态。",
                "package.json scripts、lockfile、README，确认项目类型和可用验证命令。",
                "; ".join(item for item in validation if item),
                "rg 搜索 apiKey/apiSecret/token/password、console、@ts-ignore、any、eval、dangerouslySetInnerHTML、localStorage。",
                ".env*、vite/webpack/CI/deploy 配置中的生产路径、base URL、构建入口和主题资源。",
            ),
            risk_order=("发布阻塞", "安全/密钥", "运行时缺陷", "配置/依赖一致性", "测试缺口", "代码质量", "清理项"),
        )
    return TaskStrategy(
        name="code_review",
        description="代码审查：先验证高风险事实，再读取证据文件。",
        principles=(
            "证据优先：先用搜索、状态和验证命令缩小范围。",
            "风险优先：把会阻塞运行、发布、安全和数据正确性的问题放前面。",
            "最小读取：只读取能支撑结论的文件和行号。",
        ),
        first_evidence=(
            "git status --short 和 git diff --stat。",
            "项目清单文件和已有测试/构建/检查命令。",
            "风险关键词搜索和失败命令输出。",
        ),
        risk_order=("发布/运行阻塞", "安全", "数据正确性", "回归风险", "测试缺口", "维护性"),
    )


def _bug_strategy(profile: dict) -> TaskStrategy:
    return TaskStrategy(
        name=f"{profile.get('kind', 'generic')}_bug_fix",
        description="Bug 定位：复现优先，沿报错和调用链反查。",
        principles=("复现优先：先获得错误输出或失败测试。", "根因优先：从症状反查调用链，不盲改。", "验证优先：修复后运行同一复现命令。"),
        first_evidence=("用户给出的报错/日志。", "最小复现命令或相关测试。", "rg 搜索错误符号、路由、函数名和调用链。"),
        risk_order=("可复现崩溃", "数据错误", "边界条件", "回归测试缺口"),
    )


def _release_review_strategy(profile: dict) -> TaskStrategy:
    return TaskStrategy(
        name=f"{profile.get('kind', 'generic')}_release_review",
        description="发布检查：构建、环境、依赖和 CI 优先。",
        principles=("发布验证优先：先运行可用 build/test/lint。", "环境优先：检查 .env、CI、部署配置和 base path。", "依赖一致性优先：检查 lockfile 和包管理器混用。"),
        first_evidence=("git status --short。", "package/lockfile/CI/deploy 配置。", "build、lint、test 或项目等价验证命令。", ".env* 与生产入口配置。"),
        risk_order=("构建失败", "测试失败", "环境配置错误", "依赖不一致", "发布包内容异常"),
    )


def _security_review_strategy(profile: dict) -> TaskStrategy:
    return TaskStrategy(
        name=f"{profile.get('kind', 'generic')}_security_review",
        description="安全审查：密钥、认证、权限和外部边界优先。",
        principles=("敏感信息优先：先查明文密钥、token、证书和调试输出。", "认证链路优先：检查登录、鉴权、权限边界和前后端职责。", "证据必须落到文件/行号。"),
        first_evidence=("rg 搜索 apiKey、apiSecret、token、password、Authorization、Bearer、private key。", ".env* 和前端可打包代码。", "auth/login/permission/request/http 相关模块。"),
        risk_order=("密钥暴露", "鉴权绕过", "权限越界", "敏感日志", "配置误用"),
    )


def _architecture_strategy(profile: dict) -> TaskStrategy:
    return TaskStrategy(
        name=f"{profile.get('kind', 'generic')}_architecture",
        description="架构理解：入口、路由、模块边界、数据流优先。",
        principles=("入口优先：先识别启动入口、路由和模块边界。", "关系优先：用搜索定位调用链和数据流。", "总结优先：输出结构，不展开低价值文件细节。"),
        first_evidence=("rg --files 或等价文件清单。", "README、package/manifest、入口文件、路由文件。", "核心目录和外部依赖。"),
        risk_order=("核心入口", "模块边界", "数据流", "外部依赖", "可维护性风险"),
    )


def _looks_like_frontend_package(package_json: Path) -> bool:
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    deps = {}
    for key in ("dependencies", "devDependencies"):
        value = data.get(key)
        if isinstance(value, dict):
            deps.update(value)
    names = set(deps)
    return bool(names & {"react", "vue", "svelte", "vite", "webpack", "next", "@vitejs/plugin-react"})


def _package_scripts(package_json: Path) -> list[str]:
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    return sorted(str(key) for key in scripts)


def _detect_package_manager(cwd: Path) -> str:
    if (cwd / "pnpm-lock.yaml").is_file():
        return "pnpm run"
    if (cwd / "yarn.lock").is_file():
        return "yarn"
    if (cwd / "package-lock.json").is_file():
        return "npm run"
    return "npm run"


def _first_matching_script(scripts: set[str], candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if candidate in scripts:
            return candidate
    return ""


def _is_code_review_task(task: TaskState) -> bool:
    if any(step.intent == "code_review" for step in task.plan):
        return True
    normalized = task.goal.lower()
    if any(keyword in normalized for keyword in ("修改", "修复", "新增", "删除", "生成代码", "重构")):
        return False
    return any(keyword in normalized for keyword in ("代码", "源码", "项目", "工程")) and any(
        keyword in normalized for keyword in ("审查", "问题", "风险", "清单", "质量", "看看", "分析")
    )


def _is_bug_goal(normalized_goal: str) -> bool:
    return any(keyword in normalized_goal for keyword in ("bug", "报错", "错误", "异常", "崩溃", "不生效", "定位问题"))


def _is_release_goal(normalized_goal: str) -> bool:
    return any(keyword in normalized_goal for keyword in ("发布", "上线", "构建", "打包", "部署", "ci", "uat", "prod"))


def _is_security_goal(normalized_goal: str) -> bool:
    return any(keyword in normalized_goal for keyword in ("安全", "密钥", "token", "鉴权", "权限", "泄露", "api key", "apikey"))


def _is_architecture_goal(normalized_goal: str) -> bool:
    return any(keyword in normalized_goal for keyword in ("架构", "模块关系", "调用链", "项目结构", "代码结构", "入口"))
