# Hakimi 模块目录

打开 [platforms/](platforms) 可以看到四个独立私有仓库，每个入口都固定到本目录集成验证过的提交。

| 模块 | 子模块目录 | 独立仓库 |
| --- | --- | --- |
| QQ 社交 | `platforms/qq` | [hakimi-module-qq](https://github.com/FireOpalus/hakimi-module-qq) |
| B 站巡逻 | `platforms/bilibili` | [hakimi-module-bilibili](https://github.com/FireOpalus/hakimi-module-bilibili) |
| Minecraft | `platforms/minecraft` | [hakimi-module-minecraft](https://github.com/FireOpalus/hakimi-module-minecraft) |
| 天气观测 | `platforms/weather` | [hakimi-module-weather](https://github.com/FireOpalus/hakimi-module-weather) |

本仓库维护模块清单、动作契约和调度类型。加载器读取 `.gitmodules`，按目录分别导入 `hakimi.modules.platforms.<目录名>.plugin`。不同模块可使用同名的内部文件，通过相对导入引用。原有四个模块的 `legacy-flat-imports` 用于兼容已有的 Python 导入路径。

常驻系统服务随主仓 `src/hakimi/system_modules/` 发布。启动时按依赖顺序自动注册，后台根据声明展示状态与思考场景。需要平台连接、启停开关或世界引擎动作的扩展，使用下方的平台插件入口。

[主仓](https://github.com/FireOpalus/hakimi-surf)把本目录固定在 `src/hakimi/modules`。首次克隆使用 `git clone --recurse-submodules`，已有检出在拉取后运行 `git submodule update --init --recursive`。宿主、持久化数据和部署配置由主仓管理。

单独升级一个模块时，先推送对应实现仓库的提交，然后在本仓库记录该子模块的新指针。最后在主仓运行集成测试并提交目录指针。回滚时可以选择任一先前验证过的组合。

## 新增模块

1. 在自己的模块仓库根目录放置 `plugin.py`。下面是一个最小入口，部署后会在后台出现「温室」卡片。

```python
from hakimi.modules import BaseModule


class Greenhouse(BaseModule):
    module_id = "greenhouse"
    display_name = "温室"
    description = "温室湿度与浇水提醒"
    enabled = False

    async def _raw_sensor_summary(self):
        return "等待首次湿度观测"


def create_module(service):
    return Greenhouse(service.s)
```

2. 推送模块源码，在本目录仓库执行 `git submodule add ../hakimi-module-greenhouse.git platforms/greenhouse`，提交并推送。Git 会同时更新清单和版本指针。目录名使用小写字母开头，可含小写字母、数字和下划线。选择唯一的 `module_id` 并保持稳定，SQLite 使用它关联开关和配置。
3. 私有模块首次接入 CI 时，在模块仓库授权一把只读部署公钥，并把配对私钥存为主仓的 Actions Secret。上述示例的名称为 `HAKIMI_MODULE_GREENHOUSE_DEPLOY_KEY`，其中 `GREENHOUSE` 取自大写目录名。每个下载任务只使用对应仓库的凭证。开发机和部署机也需要对该模块有读取权限。
4. 在主仓更新并提交 `src/hakimi/modules` 的指针，按正常流程发布引擎镜像。引擎在启动时读取清单并执行模块入口，标准后台卡片随注册结果自动出现。线上滑块用于启停已安装模块。多个仓库之间用固定提交发布，模块代码以引擎进程的权限运行。

模块可通过 `actions()`、`schedules()` 和 `event_rules()` 参与世界引擎。声明 `schedule_options` 即可得到后台设置表单。需要恢复本地状态或释放资源时，可覆盖异步上下文管理器 `lifespan()`，它在开关恢复后、后台工作任务启动前进入，并在服务结束时退出。

使用主模型的插件可以从 `hakimi.modules` 导入 `ThinkingScene`，并在模块类声明 `thinking_scenes = (ThinkingScene("greenhouse.report", "温室简报", default="low", output_tokens=1024),)`。生成时传入同一个 `scenario="greenhouse.report"`。注册后，「模型与思考」按模块列出场景，设置同时用于实际请求与预算预留。卸载后场景从页面移除，再次安装会恢复上次保存的档位。

如果其他模块或对话需要同步取得只读数据，可以声明允许 `"query"` 来源的无参数内部动作。其他模块通过 `result = await service.life_engine.query("weather.read")` 即可读取天气，先检查 `result.status == "done"`，再使用 `result.details["observation"]`。对话路由只会向 AI 提供当前可用的查询能力，查询同样受模块开关与单项动作开关控制。

需要专用管理接口时，`plugin.py` 还可导出仅包含 HTTP 路由的 FastAPI `admin_router`。宿主会挂载到受管理口令保护的 `/admin/api/life/modules/<目录名>` 下，并保留通用模块接口。天气模块的地点查询是一个完整示例。[天气模块入口](https://github.com/FireOpalus/hakimi-module-weather/blob/main/plugin.py)

### 社交通道

在 `chat_platforms` 注册全局唯一的协议名。完成平台的签名验证后，构造规范化的 `Message`，调用 `await service.accept_from(self, message)`。实现 `authorize_chat(message, settings, *, bot_exempt=False)` 来检查本模块的账号、群或私聊准入。后台会按注册结果生成模块入口和会话隐私设置。

在 `send_chat(message, text, *, before_send)` 中，实际提交前调用 `await before_send()`。允许提交时按字面文本发送，返回平台确认的非空消息 ID 字符串。需要投递 Owner 日报时实现 `chat_connected(platform)` 并配置一个已验证的 Owner 绑定。群记忆同步另外实现 `permits_memory_mirror(message, settings)`，按本平台授权范围核验账号和群组。准入、群镜像和主动投递资格默认关闭。插件可用 `permits_chat_export(message, consumer)` 授权特定会话供 `patrol_diary` 归档或供 `bilibili_surf` 提取选片话题。这些额外的读取权限默认关闭。

`context_data(limit=..., now=..., message=...)` 提供经过授权的只读素材，`message` 只携带目标身份，空值表示公开用途。`public_patrol_entry(event)` 可为本模块的公开事件返回稳定业务键及已审核的巡逻正文，相同业务键只归档一次。`current_activity()` 提供本地活动状态。耗费精力的动作在 `Action` 上声明 `effortful=True`，低精力时由调度器统一过滤。
