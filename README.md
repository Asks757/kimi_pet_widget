# kimi_pet_widget

一个基于 PySide6 的 Kimi 桌面宠物，支持 Linux 和 Windows。桌宠可以显示 Kimi Code 的 5 小时额度和每周额度，并在额度消耗到指定比例时自动提醒。

## 功能

- 显示 5 小时剩余额度、每周剩余额度和 5 小时额度重置倒计时。
- 每 5 分钟自动刷新；点击桌宠展开气泡时立即刷新。
- 刷新期间保留上一次成功获取的数据，新数据返回后自动更新。
- 5 小时额度每消耗 10% 自动弹出提醒，支持跨档位和重启后的进度记录。
- 支持自定义角色图片、角色命名、切换和删除，最后一张图片不可删除。
- 支持调整大小、透明度、朝向、Q 弹强度、上下浮动、边缘吸附和窗口置顶。
- 气泡支持字体扫光、自动收回及左右方向自适应。
- 透明区域点击穿透，减少对其他桌面操作的干扰。

## 环境要求

- Python 3.10 或更高版本。
- Linux 或 Windows。
- 已登录 Kimi Code，用于读取额度信息。

首次启动时，启动脚本会自动创建 `.venv` 并安装 `requirements.txt` 中的依赖。

## 安装与启动

```bash
git clone https://github.com/Asks757/kimi_pet_widget.git
cd kimi_pet_widget
```

### Linux

```bash
./run.sh
```

### Windows

双击 `run.bat`，或在命令提示符中运行：

```bat
run.bat
```

Windows 需要先从 [Python 官网](https://www.python.org/downloads/)安装 Python，安装时建议勾选“Add Python to PATH”。

## 使用方式

- **拖动桌宠**：移动位置；开启边缘吸附后自动靠近屏幕边缘。
- **单击桌宠**：展开或收回额度气泡；展开时自动刷新额度。
- **双击桌宠**：立即刷新额度。
- **右键桌宠**：打开菜单，可以刷新、设置置顶、调整透明度、进入设置或退出。
- **设置页面**：管理角色图片，并调整桌宠和气泡的显示效果。

额度数据来自 Kimi Code 的额度接口。如果无法获取额度，请确认已在 Kimi Code 中登录；登录失效时需要重新登录。

## 本地数据与隐私

角色图片和个人设置保存在项目的 `userdata/` 目录中：

- `default_settings.json`：随项目发布的默认配置。
- `roles/legacy.png`：随项目发布的默认角色。
- `settings.json`：首次运行后生成的个人配置，不会提交到 Git。
- 后续导入的角色图片：仅保存在本地，不会提交到 Git。

删除原始图片不会影响已导入的角色。项目目录中不保存 Kimi 登录凭证；程序只读取 Kimi Code 已有的登录状态来查询额度。

`.venv/`、个人设置、后续导入的角色、缓存和本地工具均已加入 `.gitignore`。

## 主要文件

```text
kimi_pet_widget/
├── whale_widget.py       # 桌宠、气泡、角色库、设置和动画
├── kimi_quota.py         # Kimi Code 额度查询
├── requirements.txt      # Python 依赖
├── run.sh                # Linux 启动脚本
├── run.bat               # Windows 启动脚本
└── userdata/             # 默认资源与本地个人数据
```
