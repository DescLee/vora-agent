from __future__ import annotations

from vora.llm import LLMResult, openai_messages
from vora.models import ToolCall


class ScriptedLLM:
    def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201
        user_text = " ".join(
            getattr(message, "content", "")
            for message in messages
            if getattr(message, "role", "") == "user"
        )
        system_text = " ".join(
            getattr(message, "content", "")
            for message in messages
            if getattr(message, "role", "") == "system"
        )
        tool_text = " ".join(
            getattr(message, "content", "")
            for message in messages
            if getattr(message, "role", "") == "tool"
        )

        chat_mode = any(keyword in system_text for keyword in ["对于闲聊", "名字和自我介绍类轻量问题"])
        if chat_mode:
            if any(keyword in user_text for keyword in ["你的名字", "你叫什么", "你是谁"]):
                content = "我叫 vora，是你的个人助理。"
            elif any(keyword in user_text for keyword in ["你好", "您好", "hello", "hi"]):
                content = "你好，我在。你可以继续说你的问题。"
            elif any(keyword in user_text for keyword in ["能力", "可以", "会不会", "能不能", "是否"]):
                content = "我可以帮你分析当前上下文、解释命令、整理思路，也可以在需要时调用项目工具。"
            else:
                content = f"我先按普通对话理解。你刚才说的是：{user_text}"
            return LLMResult(
                content=content,
                source_request={"messages": openai_messages(messages), "tool_names": list(tool_names)},
                source_response={"mode": "scripted", "content": content},
            )

        project_query = any(keyword in user_text for keyword in ["当前项目", "这个项目", "项目是做什么", "项目作用"])
        cli_issue_query = any(keyword in user_text for keyword in ["vora", "usage:", "unrecognized arguments", "argparse"])
        cli_remove_mixup = "list remove" in user_text and "vora" in user_text
        if cli_issue_query or cli_remove_mixup:
            return LLMResult(
                content=(
                    "这条命令写法有误：`remove` 是独立子命令，不能跟在 `list` 后面。"
                    "正确用法是 `vora remove <session_id>`。"
                ),
                source_request={"messages": openai_messages(messages), "tool_names": list(tool_names)},
                source_response={"mode": "scripted", "content": "cli usage guidance"},
            )

        create_hello_world_query = (
            "helloworld.py" in user_text
            and any(keyword in user_text for keyword in ["新建", "创建", "生成", "写一个"])
            and "write_file" in tool_names
        )
        if (
            create_hello_world_query
            and ("command exited" not in tool_text or "wrote helloworld.py" in tool_text)
            and "command exited 0" not in tool_text
            and "run_bash" in tool_names
        ):
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-test-helloworld",
                        name="run_bash",
                        args={
                            "command": "python helloworld.py | grep 'hello world' # test helloworld",
                        },
                    )
                ]
            )

        if create_hello_world_query and "wrote helloworld.py" not in tool_text:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-write-helloworld",
                        name="write_file",
                        args={
                            "path": "helloworld.py",
                            "content": "print('hello world')\n",
                        },
                    )
                ]
            )

        if create_hello_world_query and "wrote helloworld.py" in tool_text and "command exited 0" in tool_text:
            return LLMResult(content="已在工作目录下新建 helloworld.py，内容为 `print('hello world')`。")

        if project_query and "list_files" in tool_names and not tool_text:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-list-project",
                        name="list_files",
                        args={"path": "."},
                    )
                ]
            )

        if project_query and "read_file" in tool_names and "paths:" in tool_text and "content_ref:" not in tool_text and "# vora" not in tool_text:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-read-readme",
                        name="read_file",
                        args={"path": "README.md"},
                    ),
                    ToolCall(
                        id="call-read-pyproject",
                        name="read_file",
                        args={"path": "pyproject.toml"},
                    ),
                    ToolCall(
                        id="call-read-technical-design",
                        name="read_file",
                        args={"path": "docs/v1-technical-design.md"},
                    ),
                ]
            )

        if project_query and "read_file" in tool_names and "content_ref:" in tool_text and "# vora" not in tool_text:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-read-readme-lines",
                        name="read_file",
                        args={"path": "README.md", "start_line": 1, "limit_lines": 80},
                    ),
                    ToolCall(
                        id="call-read-pyproject-lines",
                        name="read_file",
                        args={"path": "pyproject.toml", "start_line": 1, "limit_lines": 80},
                    ),
                    ToolCall(
                        id="call-read-technical-design-lines",
                        name="read_file",
                        args={"path": "docs/v1-technical-design.md", "start_line": 1, "limit_lines": 80},
                    ),
                ]
            )

        if project_query and "# vora" in tool_text:
            return LLMResult(
                content=(
                    "这个项目是 vora，一个本地 TUI 版 Agent Runtime。"
                    "它的核心作用是让用户在终端里连续对话，驱动 Agent 通过 ReAct 循环调用文件工具，"
                    "再经过 reflection 与外层工程循环生成调研、总结或代码相关产物。"
                    "项目已经包含 prompt_toolkit TUI、OpenAI-compatible 配置、文件工具、"
                    "工具调度、长期记忆、上下文压缩基础、运行日志、产物输出和过程 trace 展示。"
                ),
                source_request={"messages": openai_messages(messages), "tool_names": list(tool_names)},
                source_response={"mode": "scripted", "content": "project summary"},
            )

        if tool_text:
            if "a.md" in user_text and "content_ref:" in tool_text and "hello world" not in tool_text:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-read-a-lines",
                            name="read_file",
                            args={"path": "a.md", "start_line": 1, "limit_lines": 80},
                        )
                    ],
                    source_request={"messages": openai_messages(messages), "tool_names": list(tool_names)},
                    source_response={"mode": "scripted", "content": "tool_call: read_file targeted"},
                )
            return LLMResult(
                content=f"已根据工具结果生成草稿：{tool_text}",
                source_request={"messages": openai_messages(messages), "tool_names": list(tool_names)},
                source_response={"mode": "scripted", "content": tool_text},
            )

        if "a.md" in user_text and "read_file" in tool_names:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-read-a",
                        name="read_file",
                        args={"path": "a.md"},
                    )
                ],
                source_request={"messages": openai_messages(messages), "tool_names": list(tool_names)},
                source_response={"mode": "scripted", "content": "tool_call: read_file"},
            )

        if "docs" in user_text and "list_files" in tool_names:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-list-docs",
                        name="list_files",
                        args={"path": "docs"},
                    )
                ],
                source_request={"messages": openai_messages(messages), "tool_names": list(tool_names)},
                source_response={"mode": "scripted", "content": "tool_call: list_files"},
            )

        return LLMResult(
            content=f"报告草稿：{user_text or '已生成'}",
            source_request={"messages": openai_messages(messages), "tool_names": list(tool_names)},
            source_response={"mode": "scripted", "content": user_text or "已生成"},
        )

    def context_limit(self) -> int | None:
        return None
