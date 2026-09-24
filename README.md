# Ensemble Beat Grid API

合奏节拍网格服务：提交一小节的**目标时长**和**至少两个声部**（每个声部是依次排列的
发声/休止事件，时长为正有理数字符串），服务用**精确分数**校验各声部总时长是否等于
目标；通过后保存小节，并可按 ID 查询由所有事件边界并集切分出的**半开时间段网格**。

技术栈：Python 3.12 · FastAPI · SQLite（标准库 `sqlite3`）· Docker / Compose

## 启动（一条命令）

```bash
docker compose up --build
```

该命令完成依赖安装、SQLite 数据库初始化（应用启动时自动建表）并启动服务。

- API 地址： http://localhost:8000
- 交互式文档（Swagger UI）： http://localhost:8000/docs

## 配置

| 环境变量         | 默认值          | 说明                     |
| ---------------- | --------------- | ------------------------ |
| `DATABASE_PATH`  | `/data/app.db`（容器内）/ `./app.db`（本地） | SQLite 数据库文件路径 |
| （端口映射）      | `8000:8000`     | 在 `docker-compose.yml` 中修改 |

Compose 中数据库文件挂载在命名卷 `beat-grid-data` 上，容器重启数据不丢失。

## API

### `POST /measures` — 提交小节

```bash
curl -s -X POST http://localhost:8000/measures \
  -H 'Content-Type: application/json' \
  -d '{
    "target_duration": "4/4",
    "voices": [
      {"name": "melody", "events": [
        {"kind": "sound", "duration": "1/2"},
        {"kind": "rest",  "duration": "1/4"},
        {"kind": "sound", "duration": "1/4"}
      ]},
      {"name": "bass", "events": [
        {"kind": "rest",  "duration": "1/4"},
        {"kind": "sound", "duration": "3/4"}
      ]}
    ]
  }'
```

- 时长格式：正有理数字符串，如 `"2"`、`"1/4"`、`"3/8"`（拒绝 `0`、负数、小数、
  分母为 0）。所有时长以 `fractions.Fraction` 精确累加，无浮点误差。
- 任一一声部总时长 ≠ 目标时长时返回 **422**，逐声部给出 `expected` / `actual` /
  `difference`（= actual − expected，负值表示不足），且**不保存任何数据**：

```json
{
  "detail": {
    "message": "voice durations do not add up to the target duration; measure was not saved",
    "mismatches": [
      {"voice": "melody", "expected": "1", "actual": "3/4", "difference": "-1/4"}
    ]
  }
}
```

- 全部匹配时返回 **201** 及保存的小节（含每个事件的 ID `v{声部序号}e{事件序号}`、
  规范化的起止偏移）。

### `GET /measures/{id}` — 查询小节

返回小节、各声部及全部事件（种类、时长、起止偏移）。

### `GET /measures/{id}/grid` — 查询节拍网格

对所有事件的起止点取并集，按相邻点生成**半开时间段** `[start, end)`；每段列出各声部
正在发声的事件 ID，休止为 `null`。半开区间保证相邻事件边界处既不丢音也不重复计音：

```json
{
  "measure_id": 1,
  "target_duration": "1",
  "voices": ["melody", "bass"],
  "segments": [
    {"start": "0",   "end": "1/4", "events": {"melody": "v1e1", "bass": null}},
    {"start": "1/4", "end": "1/2", "events": {"melody": "v1e1", "bass": "v2e2"}},
    {"start": "1/2", "end": "3/4", "events": {"melody": null,   "bass": "v2e2"}},
    {"start": "3/4", "end": "1",   "events": {"melody": "v1e3", "bass": "v2e2"}}
  ]
}
```

### `GET /measures/{id}/rhythm-matches?voice={声部名}` — 检索节奏等价声部

以指定小节中的一个声部为**源声部**，在全部已保存小节中检索与它**节奏等价**的其他声部
（不含源声部自身）。两个声部节奏等价，当且仅当存在某个循环位移 `shift`（0 ≤ shift < N）
使下列条件同时成立：

1. 两段事件**数量相同**（都为 N）；
2. 候选的第 `j` 个事件与源的第 `(j + shift) mod N` 个事件**发声/休止种类逐项一致**；
3. 每一对对应事件的**时长之比都是同一个正有理数** `ratio`（= 候选时长 / 源时长，即整体
   变速比例；`"1"` 表示速度不变）——允许整体拉伸或压缩，但不允许内部相对节奏变形。

全部比较使用 `fractions.Fraction` **精确分数**完成，无浮点误差。若有多个可行位移，
取**最小**的 `shift`（从 0 起逐个尝试）。

- 查询参数 `voice` 必填，值为源声部名；源小节不存在、或该声部名不在源小节中时返回
  **404**；缺少 `voice` 参数返回 **422**。
- 结果按**小节 ID 升序**排列，同一小节内按声部的**原始保存顺序**排列；没有匹配时
  `matches` 为空数组。

```bash
curl -s 'http://localhost:8000/measures/1/rhythm-matches?voice=lead'
```

例：源小节声部 `lead` 为「发声 1/4、休止 1/4、发声 1/2」，另一小节的 `echo` 为
「发声 1、发声 1/2、休止 1/2」——整体时长放大 2 倍且循环位移 2 位后逐项对应：

```json
{
  "measure_id": 1,
  "voice": "lead",
  "matches": [
    {"measure_id": 1, "voice": "twin", "ratio": "1", "shift": 0},
    {"measure_id": 2, "voice": "echo", "ratio": "2", "shift": 2}
  ]
}
```

各字段含义：

| 字段         | 说明                                                                 |
| ------------ | -------------------------------------------------------------------- |
| `measure_id` | 候选小节 ID                                                          |
| `voice`      | 候选声部名（仅按 `(小节 ID, 声部名)` 排除源声部自身；同名的其他声部仍正常参与匹配） |
| `ratio`      | 候选/源的统一时长比例（正有理数字符串），`"2"` 表示候选整体慢一倍     |
| `shift`      | 循环位移位数：候选第 `j` 个事件对应源第 `(j + shift) mod N` 个事件，取最小可行值 |

### `POST /loop-alignments` — 两小节循环对齐（排练用）

接收两个**已保存小节**的 ID（同一小节可同时作为 A、B），把它们当作两条不同长度的
节奏循环，用精确分数求两条循环**首次同时回到起点**的时刻（两小节时长的最小公倍数），
并把两条循环的事件分别重复到该时刻。时间线按两条循环**全部事件边界**的并集切成
半开区间；每段分别在 `a` / `b` 下按各声部给出正在发声事件的**原事件 ID** 和
**从 1 开始的循环序号**，休止为 `null`。

- 任一小节 ID 不存在返回 **404**（detail 指明是 `a` 还是 `b`），不产生时间线。
- 展开的事件副本总数（= A 重复次数 × A 事件数 + B 重复次数 × B 事件数）超过
  **2000** 时返回 **422**，detail 给出 `expanded_events` / `limit` / `horizon` /
  `repetitions` 等信息，**不输出任何截断的时间线**（响应中没有 `segments`）。

```bash
curl -s -X POST http://localhost:8000/loop-alignments \
  -H 'Content-Type: application/json' \
  -d '{"a_measure_id": 1, "b_measure_id": 2}'
```

例：A 小节时长 `1`、B 小节时长 `3/2`，首次重合在 `3`（A 重复 3 次、B 重复 2 次）：

```json
{
  "a_measure_id": 1,
  "b_measure_id": 2,
  "horizon": "3",
  "length_a": "1",
  "length_b": "3/2",
  "repetitions": {"a": 3, "b": 2},
  "voices": {"a": ["melody", "bass"], "b": ["melody", "bass"]},
  "expanded_events": 21,
  "segments": [
    {
      "start": "0",
      "end": "1/4",
      "a": {"melody": {"event_id": "v1e1", "loop": 1}, "bass": null},
      "b": {"melody": {"event_id": "v1e1", "loop": 1}, "bass": null}
    },
    {
      "start": "1",
      "end": "5/4",
      "a": {"melody": {"event_id": "v1e1", "loop": 2}, "bass": null},
      "b": {"melody": null, "bass": null}
    }
  ]
}
```

`event_id` 是事件在**原小节**中的 ID（如 `v1e1`，与 `GET /measures/{id}` 返回的一致）；
`loop` 表示该发声来自第几次循环。半开区间 `[start, end)` 保证循环边界处既不丢音也不
重复计音：恰好在某次循环起点休止的声部，在该起点的段中为 `null`。

> 在浏览器中打开 http://localhost:8000/ 可查看服务与接口清单，
> http://localhost:8000/docs 为可直接发起请求的 Swagger UI。

## 本地开发与测试

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # 本地启动（默认 ./app.db）
pytest                                 # 全部测试（纯逻辑 + API）
python3 -m unittest discover -s tests -p 'test_grid.py'   # 仅核心逻辑（零依赖）
```

## 项目结构

```
app/
  grid.py      # 核心逻辑：有理数解析、精确累加、边界并集切分网格、节奏等价匹配、循环对齐（纯标准库）
  db.py        # SQLite 连接、建表、读写（纯标准库）
  schemas.py   # Pydantic 请求/响应模型与校验
  main.py      # FastAPI 应用与路由（启动时初始化数据库）
tests/         # 核心逻辑测试（stdlib 可跑）+ API 测试（TestClient）
Dockerfile
docker-compose.yml
```
