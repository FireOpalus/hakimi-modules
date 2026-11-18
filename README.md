# Hakimi 模块目录

打开 [platforms/](platforms) 可以看到四个独立私有仓库，每个入口都固定到本目录集成验证过的提交。

| 模块 | 子模块目录 | 独立仓库 |
| --- | --- | --- |
| QQ 社交 | `platforms/qq` | [hakimi-module-qq](https://github.com/FireOpalus/hakimi-module-qq) |
| B 站巡逻 | `platforms/bilibili` | [hakimi-module-bilibili](https://github.com/FireOpalus/hakimi-module-bilibili) |
| Minecraft | `platforms/minecraft` | [hakimi-module-minecraft](https://github.com/FireOpalus/hakimi-module-minecraft) |
| 天气观测 | `platforms/weather` | [hakimi-module-weather](https://github.com/FireOpalus/hakimi-module-weather) |

本仓库维护模块注册、动作契约和调度类型。各模块通过共同的 Python 包 `hakimi.modules` 提供能力，例如 `hakimi.modules.qq_social` 和 `hakimi.modules.weather`。模块实现的文件名在这个包内需保持唯一。

[主仓](https://github.com/FireOpalus/hakimi-surf)把本目录固定在 `src/hakimi/modules`。首次克隆使用 `git clone --recurse-submodules`，已有检出在拉取后运行 `git submodule update --init --recursive`。宿主、持久化数据和部署配置由主仓管理。

单独升级一个模块时，先推送对应实现仓库的提交，然后在本仓库记录该子模块的新指针。最后在主仓运行集成测试并提交目录指针。回滚时可以选择任一先前验证过的组合。
