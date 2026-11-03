# 回归模型清单（M6）

> 每次发版**必跑**的 6 个样本。用 `python tools/bench.py --all` 一次跑完，
> 结果写入 `docs/benchmark-v1.1.md`。
> 「关注点」= 这一行用来守住什么不退化；「GLB 期望结构」= 达标的最低门槛。

| # | 类别 | UID / 链接 | 关注点 | GLB 期望结构 |
|---|------|-----------|--------|--------------|
| 1 | 短链（跳转解析） | https://skfb.ly/p9HSv | 短链 302 后能否取到 32 位 UID | meshes>=1 |
| 2 | 短链（跳转解析） | https://skfb.ly/6S7uo | 同上，覆盖另一条短链路径 | meshes>=1 |
| 3 | 已知可用样本 | 1053729ad9d24d71a578622251f76deb | 全链路基线（下载/解密/转换） | meshes>=1 |
| 4 | 已知可用样本 | 7c60b4d1b8ab4187965f30c5e0212fc0 | 全链路 + 贴图还原 | images>=1 |
| 5 | 带骨骼/动画：S.W.A.T. Operator（QA 严过关 2026-11-01 补选） | 9e82fabf26194896b5ad4a364d864eab | `skins>=1` 不退化（v1.3 重写 GLB 时的核心风险） | skins>=1 |
| 6 | 材料数 ≥5：Chibi Gear Solid（QA 严过关 2026-11-01 补选） | a4ca0e3864e143af9bf1dffc7fc8029b | 材质数与贴图通道一致 | materials>=5 |

## 补选说明（2026-11-01 QA 实测）

- 第 5 行选 **S.W.A.T. Operator**（106,714 面，50 贴图）：GLB 实测 `skins=35, materials=12`，
  是真正的骨骼蒙皮角色；Buster Drone（294e7965…）虽标记动画但属节点动画
  （osgjs 无 bone/skinning 数据，v1.0.0/v1.1.0 输出 skins=0 一致），不满足本行目的，弃用。
- 第 6 行选 **Chibi Gear Solid**（34,994 面，60 贴图）：GLB 实测 `materials=60, images=60`。
  首选候选 The Noble Craftsman（0e8ff87f…）只有 1 个材质（贴图图集合并），不满足 ≥5，弃用。

## 说明

- 第 1~4 行是已知可用的 UID/短链，用于托底（保证套件本身不是全红）。
- 第 5~6 行为**待补选**：`tools/bench.py --all` 遇到空 UID 会跳过并打印
  「待所有者补全」，不会当成失败。
- 为什么必须补第 5、6 行：v1.1 把引擎的解密路径从 legacy 切到了 WASM（PATH 修复的
  副作用），要证明「切了也没坏」；而 v1.3 要用 Python 重写 GLB 写出器，骨骼/动画与
  多材质是最容易退化的两处，没有样本就没有刹车。
- 期望结构的判定口径见 `docs/benchmark-v1.1.md` 的 meshes / images 列。
