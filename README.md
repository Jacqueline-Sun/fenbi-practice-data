# 粉笔练习数据 Power BI 可视化（全云端版）

自动采集粉笔 App 个人练习数据，保存为 CSV 文件，Power BI 通过 Web 连接器读取，实现每日自动更新可视化报表。

**全云端运行，无需电脑开机，无需网关，无需 Azure AD。**

## 架构

```
GitHub Actions (每日定时触发)
    ↓
Python 脚本 (云端运行)
    ↙
粉笔 API
(采集数据)
    ↓
CSV 文件 (提交到 GitHub 仓库)
    ↓
Power BI Web 连接器 (读取 raw URL)
    ↓
Power BI 报表 (计划刷新，自动更新)
```

| 组件 | 说明 | 费用 |
|------|------|------|
| GitHub Actions | 云端定时运行 Python 脚本 | 免费 (2000 分钟/月) |
| 粉笔 API | 数据源，RSA 加密登录 | 免费 |
| Power BI Web 连接器 | 读取 GitHub 上的 CSV 文件 | 需要 Power BI Pro |
| Power BI 计划刷新 | 每日自动刷新数据（Web 是云端数据源，无需网关） | 包含在 Pro 中 |

## 数据字段（17 列）

| 字段 | 说明 | 示例 |
|------|------|------|
| 题目编号 | 粉笔题目唯一 ID | 1234567 |
| 题目链接 | 粉笔网页版练习报告链接（可点击） | https://www.fenbi.com/spa/tiku/... |
| 所属模块 | 题目所属大类 | 言语理解 |
| 子模块 | 题目所属小类 | 逻辑填空 |
| 题目类型 | 题型 | 单选题 |
| 练习时间 | 做题时间（北京时间） | 2024-01-15 10:30:00 |
| 用户答案 | 你的选择 | A |
| 正确答案 | 标准答案 | C |
| 是否正确 | 正确 / 错误 | 错误 |
| 题目来源 | 省份或考试名称 | 国考 |
| 题目年份 | 试卷年份 | 2024 |
| 考试类型 | 公务员/事业编等 | 公务员 |
| 难度 | 题目难度系数 | 0.65 |
| 试卷名称 | 试卷完整名称 | 2024 国考行测 |
| 来源分类 | 按省份/国考/省考分类 | 国考 |
| 题库类型 | 行测/申论 | 行测 |
| 采集时间 | 本次数据采集时间 | 2024-01-15 06:00:00 |

## 设置步骤

### 第 1 步：配置 GitHub Secrets

仓库已创建好，只需添加 2 个 Secrets：

1. 进入仓库 **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**，添加：

   | Secret 名称 | 值 |
   |-------------|-----|
   | `FENBI_PHONE` | 你的粉笔手机号 |
   | `FENBI_PASSWORD` | 你的粉笔密码 |

### 第 2 步：首次运行

1. 在 GitHub 仓库页面点击 **Actions** 标签
2. 左侧选择 **Fenbi Daily Sync** 工作流
3. 点击 **Run workflow** → **Run workflow** 手动触发
4. 等待运行完成（约 2-5 分钟）
5. 确认 `data/practice_history.csv` 文件已生成

如果运行失败，点击进入运行详情查看日志，常见原因：
- 粉笔账号密码错误 → 检查 `FENBI_PHONE` / `FENBI_PASSWORD`
- 粉笔 API 结构变化 → 把日志发给我调整字段映射

### 第 3 步：在 Power BI Desktop 中连接 CSV

1. 打开 Power BI Desktop
2. 点击 **获取数据** → **Web**
3. 输入 CSV 文件的 raw URL（替换 `{用户名}` 为实际 GitHub 用户名）：
   ```
   https://raw.githubusercontent.com/{用户名}/fenbi-practice-data/main/data/practice_history.csv
   ```
4. 点击 **确定**
5. Power BI 会自动识别 CSV 格式和中文列名
6. 点击 **加载**

### 第 4 步：创建报表并发布

1. 在 Power BI Desktop 中创建可视化图表
2. 点击 **发布** → 选择工作区
3. 登录 Power BI Service 账号

**推荐图表：**
- 各模块正确率（柱状图：所属模块 vs 是否正确）
- 练习时间趋势（折线图：练习时间 vs 记录数）
- 来源分布（饼图：题目来源）
- 年份分布（柱状图：题目年份）
- 难度 vs 正确率（散点图）
- 最近练习明细（表格：含题目链接可点击）

**推荐度量值：**
```dax
总题数 = COUNTROWS('practice_history')

正确数 = CALCULATE(COUNTROWS('practice_history'), 'practice_history'[是否正确] = "正确")

正确率 = DIVIDE([正确数], [总题数], 0)
```

### 第 5 步：设置计划刷新

1. 在 Power BI Service 中找到你发布的数据集
2. 点击 **...** → **设置**
3. 展开 **计划的刷新**
4. 启用刷新，设置每天刷新时间（如每天 7:00）
5. **无需网关** — Web 数据源是云端数据源，Power BI Service 直接访问

## 修改运行时间

GitHub Actions 的定时任务使用 UTC 时间。修改 `.github/workflows/daily-sync.yml` 中的 cron 表达式：

| 北京时间 | UTC 时间 | cron 表达式 |
|---------|---------|------------|
| 06:00 | 22:00 (前一天) | `0 22 * * *` (默认) |
| 08:00 | 00:00 | `0 0 * * *` |
| 12:00 | 04:00 | `0 4 * * *` |
| 22:00 | 14:00 | `0 14 * * *` |

注意：GitHub Actions 的定时任务可能有 5-30 分钟的延迟。

## 本地测试（可选）

1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```

2. 在 `config.ini` 中填写配置：
   ```ini
   [FENBI]
   phone = 你的手机号
   password = 你的粉笔密码
   ```

3. 运行：
   ```bash
   python main.py
   ```

## 故障排查

### GitHub Actions 未触发
- 确认仓库是 Active 状态（60 天无活动的工作流会被自动暂停）
- 在 Actions 页面手动触发一次确认工作流正常
- 检查 cron 表达式语法

### CSV 文件未更新
- 检查 GitHub Actions 运行日志
- 确认 FENBI_PHONE 和 FENBI_PASSWORD Secrets 已正确设置
- 粉笔可能更新了加密算法或 API 接口

### Power BI 刷新失败
- 确认 CSV 的 raw URL 正确
- 确认数据源凭据已配置（匿名访问即可，因为仓库是 public）
- 查看 Power BI Service 中的刷新历史记录

### 粉笔 API 返回数据异常
- 首次运行时 API 返回的字段结构可能与预期不同
- 把 GitHub Actions 运行日志发给我，根据实际返回调整字段映射

## 文件说明

| 文件 | 作用 |
|------|------|
| `main.py` | 主脚本：读配置 → 登录粉笔 → 采集数据 → 保存 CSV |
| `fenbi_client.py` | 粉笔 API 客户端：RSA 加密登录、获取题库分类、试卷列表、练习详情 |
| `data_extractor.py` | 数据转换：提取 17 个字段、时间戳转北京时间、去重、CSV 输出 |
| `.github/workflows/daily-sync.yml` | GitHub Actions 定时任务配置 |
| `requirements.txt` | Python 依赖 |
| `config.ini` | 本地测试配置（云端使用 GitHub Secrets） |
| `data/practice_history.csv` | 生成的练习数据文件（Power BI 读取此文件） |
