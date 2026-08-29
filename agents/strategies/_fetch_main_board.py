"""Fetch the full main-board A-share code list and cache it locally.

Main board = 10% daily limit.
  SH: 600 / 601 / 603 / 605   (exclude 科创板 688 = 20%)
  SZ: 000 / 001 / 002 / 003   (exclude 创业板 300/301 = 20%)
  Exclude 北交所 8xxxxx (30%) and ST (5%).

We use akshare's SSE/SZSE-only listing endpoints to avoid the bse.cn host
that is unreachable through the sandbox proxy.
"""
import json
import os
import sys

import akshare as ak

OUT = os.path.join(os.path.dirname(__file__), "main_board_codes.json")


def sh_codes():
    df = ak.stock_info_sh_name_code()
    # Columns vary; normalize to (code, name)
    code_col = "证券代码" if "证券代码" in df.columns else df.columns[0]
    name_col = "证券简称" if "证券简称" in df.columns else df.columns[1]
    out = []
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        name = str(r[name_col]).strip()
        out.append((code, name))
    return out


def sz_codes():
    df = ak.stock_info_sz_name_code()
    code_col = "A股代码" if "A股代码" in df.columns else df.columns[0]
    name_col = "A股简称" if "A股简称" in df.columns else df.columns[1]
    out = []
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        name = str(r[name_col]).strip()
        out.append((code, name))
    return out


def is_st(name: str) -> bool:
    n = name.upper()
    return "ST" in n or "退" in n or "*" in n


def to_thscode(code: str) -> str:
    # 6-digit code -> exchange suffix
    if code.startswith("6"):
        return code + ".SH"
    return code + ".SZ"


def main():
    sh = sh_codes()
    sz = sz_codes()
    print(f"raw SH rows={len(sh)} SZ rows={len(sz)}", file=sys.stderr)

    keep = []
    for code, name in sh + sz:
        if len(code) != 6 or not code.isdigit():
            continue
        if is_st(name):
            continue
        prefix = code[:3]
        if code.startswith("6"):
            if prefix not in ("600", "601", "603", "605"):
                continue
        else:
            if prefix not in ("000", "001", "002", "003"):
                continue
        keep.append((to_thscode(code), name))

    keep.sort(key=lambda x: x[0])
    print(f"main-board count={len(keep)}", file=sys.stderr)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(
            {"count": len(keep), "codes": [c for c, _ in keep],
             "named": [{"code": c, "name": n} for c, n in keep]},
            f, ensure_ascii=False, indent=0,
        )
    print(f"wrote {OUT}", file=sys.stderr)
    # preview
    print("preview first 10:", keep[:10], file=sys.stderr)


if __name__ == "__main__":
    main()
