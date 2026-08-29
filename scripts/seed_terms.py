"""预置电子行业术语表种子（v1 首批，惯用译法而非字面直译）。
用法：.venv/Scripts/python scripts/seed_terms.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

SEED = [
    # 器件
    ("capacitor", "电容", "被动元件"), ("resistor", "电阻", "被动元件"),
    ("inductor", "电感", "被动元件"), ("diode", "二极管", "器件"),
    ("transistor", "晶体管", "器件"), (" MOSFET", "MOSFET", "器件"),
    ("microcontroller", "微控制器", "器件"), ("oscillator", "振荡器", "器件"),
    ("crystal", "晶振", "器件"), ("fuse", "保险丝", "器件"),
    # 电源
    ("dropout voltage", "压差", "电源"),
    ("low-dropout regulator", "低压差线性稳压器", "电源"),
    ("linear regulator", "线性稳压器", "电源"),
    ("switching regulator", "开关稳压器", "电源"),
    ("power good", "电源正常", "电源"),
    ("power-on-reset", "上电复位", "电源"),
    ("ripple", "纹波", "电源"), ("efficiency", "效率", "电源"),
    # 信号/接口
    ("pull-up resistor", "上拉电阻", "信号"),
    ("pull-down resistor", "下拉电阻", "信号"),
    ("decoupling capacitor", "去耦电容", "信号"),
    ("bypass capacitor", "旁路电容", "信号"),
    ("ground plane", "地平面", "PCB"),
    ("solder mask", "阻焊层", "PCB"),
    ("silkscreen", "丝印", "PCB"),
    ("through-hole", "通孔", "PCB"),
    # 测试/质量
    ("thermal resistance", "热阻", "测试"),
    ("electrostatic discharge", "静电放电", "测试"),
    ("mean time between failures", "平均无故障时间", "测试"),
    ("typical", "典型值", "参数"),
    ("absolute maximum ratings", "绝对最大额定值", "参数"),
]


def main() -> None:
    for src, tgt, cat in SEED:
        db.add_term(src.strip(), tgt, cat, is_custom=False)
    n = len(db.list_terms())
    print(f"seeded {len(SEED)} terms; total {n}")


if __name__ == "__main__":
    main()
