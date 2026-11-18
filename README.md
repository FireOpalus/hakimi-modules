# Hakimi 能力模块

此仓库维护 QQ 社交、B 站巡逻、Minecraft 与天气的模块声明、动作和调度，以及它们共用的注册接口。OneBot 与官方 QQ 收发、Owner 主动私信、B 站公开读取、小号巡逻与观看历史的实现也位于这里。运行宿主、凭证、数据库与部署配置在私有主仓 FireOpalus/hakimi-surf 中。

主仓将本仓库固定在 `src/hakimi/modules`。开发时从主仓执行 `git submodule update --init --recursive`，在模块目录创建分支修改，然后回到主仓运行 `uv run pytest -q`。先推送模块提交，再在主仓提交并推送更新后的子模块指针。
