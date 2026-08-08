# RootCause Agent 评测工程 (Evaluation Project)

独立可复现的评测工程 —— 复现 [docs/evaluation.md](../docs/evaluation.md) 的全部结果。
评测流程遵循 [docs/evaluation-spec.md](../docs/evaluation-spec.md)。

## 评测覆盖

| 评测 | 数据集 | case 数 | 脚本 |
|---|---|---|---|
| LogDx-CI 信号召回 + token 压缩率 | LogDx-CI（FSE 2025） | 35 | `logdx_eval.py` |
| RCAEval 指标型定位（re1ob/re1ss） | RCAEval RE1 | 250 | `rcaeval_metric.py` |
| RCAEval 多源定位（re2ob/re2ss/RE3） | RCAEval RE2/RE3 | 271 | `rcaeval_multisource.py` |
| RCAEval 官方 baseline | RCAEval 全系 | 431 | `baseline_runner.py` + `score.py` |

## 环境

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# 可选：LLM 定位需要 DEEPSEEK_API_KEY（或其它 OpenAI 兼容 key）
export DEEPSEEK_API_KEY=sk-...
```

## 数据准备

### LogDx-CI

```bash
git clone https://github.com/eyuansu62/LogDx.git
export LOGDX_CI_ROOT=/path/to/LogDx
```

### RCAEval

```bash
# 方式一：官方 Zenodo（推荐，全量）
curl -L -o RE1-OB.zip "https://zenodo.org/records/14590730/files/RE1-OB.zip?download=1"
# ... RE1-SS / RE2-OB / RE2-SS 同理
unzip -q RE1-OB.zip -d data/RE1-OB
# 方式二：HF（RE3 parquet 格式，多源评测用）
# https://huggingface.co/datasets/phamquiluan/RCAEval
# 方式三：RCAEval 官方 main.py 自带 download_*_dataset()

# 官方 baseline 需要 RCAEval 仓库
git clone https://github.com/phamquiluan/RCAEval.git
pip install causal-learn  # circa 依赖（包名 causal-learn）
```

## 运行

```bash
# 1. LogDx-CI（静态确定性评分）
python3 logdx_eval.py --logdx-root /path/to/LogDx

# 2. RCAEval 指标型（ours，re1ob/re1ss）
python3 rcaeval_metric.py --data-dir data/RE1-OB --limit 125 --out results/ours_re1ob.json

# 3. RCAEval 多源（ours，re2ob/re2ss/RE3）
python3 rcaeval_multisource.py --data-dir data/RE2-OB --out results/ours_re2ob.json

# 4. RCAEval 官方 baseline（需 RCAEval 仓库）
python3 baseline_runner.py --rcaeval-dir /path/to/RCAEval --dataset re1-ob --method baro
python3 score.py --result-dir /path/to/RCAEval/output/results
```

## 输出

- 每个 case 的原始预测落盘 `results/*.json`（可审计）；
- 汇总指标打印并写入 `results/summary.json`；
- 双指标（准确率/召回率 + token 压缩率）一并输出。

## 竞品接入说明

| 竞品 | 要求 | 说明 |
|---|---|---|
| rtk | `rtk` 二进制在 PATH | 0.44.2，`rtk log/err cat` 文件输入 |
| headroom | `pip install headroom-ai[all]` + Kompress 模型自动下载 | 真实 Kompress，勿用截断 fallback |
| drain3 | `pip install drain3` | TemplateMiner |
| grep/tail/raw | 内置 | 基线实现见 `competitors.py`（产品仓库） |
| RCAEval 官方 | RCAEval 仓库 + 依赖 | `main.py --method <m> --dataset <ds>` |

不可运行标注（遵循评测规范 4.2）：
- LLMLingua：tiktoken 编码文件（Azure blob）+ Llama-2-7B 13GB 双重阻塞；
- microrank/tracerca：需 traces，RE1 无此数据。

## 复现结果对照

| 关键数字 | 值 |
|---|---|
| LogDx-CI ours 召回 | 0.9296（压缩 94.9%） |
| re1ob ours AC@1 | 94.4%（baro 73.6%） |
| re1ss ours AC@1 | 96.8%（nsigma 60.8%） |
| re2ob ours AC@1 | 100.0%（nsigma 78.9%） |
| re2ss ours AC@1 | 92.2%（nsigma 85.6%） |
| RE3 全量 ours | 94.4%（tail/drain 91.1%） |
