import os
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "sim.db"

FUYAO_BASE_URL = os.environ.get("FUYAO_BASE_URL", "https://fuyao.aicubes.cn")
# 必须通过环境变量注入，避免把凭据提交到版本库。
FUYAO_API_KEY = os.environ.get("FUYAO_API_KEY", "")

# 交易规则参数
COMMISSION_RATE = 0.00025      # 佣金万2.5
COMMISSION_MIN = 5.0           # 最低5元
STAMP_TAX_RATE = 0.0005        # 印花税（卖出）万5
SLIPPAGE = 0.0                 # 滑点（比例），默认0
LOT_SIZE = 100                 # 买入整手

# 涨跌停幅度
LIMIT_PCT_MAIN = 0.10
LIMIT_PCT_CHINEXT_STAR = 0.20  # 创业板300/301、科创板688
