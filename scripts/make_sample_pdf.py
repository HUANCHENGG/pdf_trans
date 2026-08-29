"""生成 3 页电子手册风格样例 PDF，用于 M0/管线验证（无需真实厂商文件）。

内容刻意包含翻译红线敏感物：带单位数值、封装型号、总线名、引脚表、图片。
用法：.venv/Scripts/python scripts/make_sample_pdf.py [输出路径]
"""
from __future__ import annotations

import sys
from pathlib import Path

from fpdf import FPDF


def build(path: Path) -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)

    # --- 第 1 页：标题 + 概述 + 参数表 ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "GP8201S Low-Dropout Voltage Regulator", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, (
        "The GP8201S is a high-accuracy linear regulator with a typical dropout voltage of "
        "280 mV at 150 mA load current. The output voltage is factory-trimmed to 3.3 V "
        "(accuracy +/-1%). The device is available in a SOP-8 package and operates over the "
        "full temperature range of -40 C to +125 C. An integrated I2C interface allows "
        "dynamic adjustment of the output voltage through register REG_VOUT.")
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Table 1. Absolute Maximum Ratings", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    rows = [
        ("Parameter", "Min", "Typ", "Max", "Unit"),
        ("Input voltage VIN", "-0.3", "-", "6.0", "V"),
        ("Output current IOUT", "0", "150", "300", "mA"),
        ("Thermal resistance", "-", "90", "-", "degC/W"),
        ("ESD rating (HBM)", "-", "-", "2000", "V"),
    ]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            w = 38 if c == 0 else 22
            style = "B" if r == 0 else ""
            pdf.set_font("Helvetica", style, 10)
            pdf.cell(w, 8, cell, border=1)
        pdf.ln(8)

    # --- 第 2 页：引脚定义 + 图片 ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Pin Description", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pins = [
        ("Pin Name", "Description"),
        ("VIN", "Power input. Bypass with a 1 uF ceramic capacitor to GND."),
        ("GND", "Ground reference."),
        ("EN", "Enable input. Pull high to enable; internal 100 kOhm pull-down."),
        ("FB", "Feedback input. Connect to the center tap of the output divider."),
        ("NC", "No internal connection. May be tied to GND for thermal relief."),
    ]
    for r, (a, b) in enumerate(pins):
        pdf.set_font("Helvetica", "B" if r == 0 else "", 10)
        pdf.cell(28, 8, a, border=1)
        pdf.cell(0, 8, b, border=1, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Figure 1. Typical Application Circuit", new_x="LMARGIN", new_y="NEXT")
    # 模拟电路图：矩形芯片 + 连线 + 电容符号
    pdf.rect(80, 120, 50, 30)
    pdf.set_font("Helvetica", "", 8)
    pdf.text(90, 138, "GP8201S")
    pdf.line(60, 135, 80, 135)   # VIN
    pdf.text(45, 136, "VIN 5V")
    pdf.line(130, 135, 150, 135) # VOUT
    pdf.text(152, 136, "VOUT 3.3V")
    pdf.rect(60, 128, 6, 14)     # 输入电容
    pdf.line(70, 135, 74, 135)
    pdf.circle(160, 135, 3, style="D")  # 输出节点
    pdf.line(140, 160, 170, 160) # 地线
    pdf.text(60, 175, "Figure notes: CIN = 1 uF X7R ceramic capacitor.")

    # --- 第 3 页：寄存器表（等宽风格内容） ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Register Map (via I2C, 7-bit address 0x58)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Courier", "", 9)
    regs = [
        ("Addr", "Register", "Description"),
        ("0x00", "REG_VOUT", "Output voltage setting. Step = 10 mV/LSB. Default 0xD2 = 3.3 V."),
        ("0x01", "REG_CTRL", "Control bits: [7] EN, [6:4] MODE, [3:0] reserved."),
        ("0x02", "REG_STAT", "Status flags: [0] OCP, [1] OTP, [2] PG (power good)."),
        ("0x03", "REG_TEMP", "Die temperature, 1 degC/LSB, two's complement."),
    ]
    for r, (a, b, c) in enumerate(regs):
        pdf.cell(20, 8, a, border=1)
        pdf.cell(30, 8, b, border=1)
        pdf.cell(0, 8, c[:52], border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, (
        "Note: All registers retain their values while VIN remains above 2.5 V. "
        "After power-on-reset, REG_VOUT reloads its default value within 200 us. "
        "The I2C interface supports standard mode (100 kHz) and fast mode (400 kHz) "
        "with an internal pull-up of 50 kOhm on SDA and SCL lines.")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    print(f"sample written: {path}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/samples/sample_datasheet.pdf")
    build(out)
