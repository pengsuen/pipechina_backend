"""Generate a realistic, internally consistent Chinese operations corpus."""

# ruff: noqa: E501 -- long source lines are intentional document paragraphs.

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import textwrap
from pathlib import Path

DEFAULT_OUTPUT = Path("dev/fixtures/knowledge_corpus")
KINDS = [
    ("standard", 12, 4, "QH-STD", "运行管理规范"),
    ("manual", 12, 3, "QH-MAN", "维护手册"),
    ("procedure", 10, 3, "QH-OPS", "巡检与异常处置规程"),
    ("case", 10, 2, "QH-INC", "信号波动事件调查报告"),
    ("handover", 8, 2, "QH-HOV", "白班交接班记录"),
    ("notice", 8, 1, "QH-NOT", "专项检查通知"),
]
STATIONS = [
    ("临川输气站", "LC", "周岚"),
    ("北岑分输站", "BC", "沈晖"),
    ("河源压气站", "HY", "蒋宁"),
    ("青岳末站", "QY", "顾远"),
    ("栖霞清管站", "QX", "许澄"),
    ("东澜计量站", "DL", "孟川"),
]
EQUIPMENT = ["过滤分离器", "调压撬", "计量撬", "压缩机组", "清管收发球筒", "阀室远传单元"]


def _body(
    kind: str,
    code: str,
    title: str,
    station: str,
    station_code: str,
    owner: str,
    equipment: str,
    index: int,
    version: int,
) -> str:
    date = f"202{4 + version}-{index % 9 + 1:02d}-01"
    common = f"""# {title}

文件编号：{code}    版本：{version}.0    生效日期：{date}
编制部门：运行管理部    归口单位：{station}    文件负责人：{owner}

## 修订记录

|版本|日期|修订说明|批准人|
|---|---|---|---|
|{version}.0|{date}|{"首次发布" if version == 1 else "修订职责分工、复核时限和记录要求"}|韩启明|

## 目的和范围

本文件规定{station}{equipment}相关记录、复核、报告和闭环要求，适用于当班运行人员、设备管理人员和审核人员。涉及设备隔离、带压作业或联锁变更时，应转交持证人员依据经批准的专项作业票执行。
"""
    sections = {
        "standard": f"""## 管理要求

运行记录按班次形成，记录时间精确到分钟。设备状态、报警来源和处置结论应分别记录，不得以推测替代现场证据。{station_code}-{index:02d}号设备的资料归档期限为{5 + index % 3}年。

## 质量指标

|指标|目标值|统计周期|责任岗位|
|---|---:|---|---|
|记录完整率|不少于 {96 + index % 4}%|月度|值班工程师|
|复核及时率|不少于 98%|月度|运行主管|
|未关闭问题|不超过 {index % 3 + 1} 项|每周|设备管理员|

发现记录冲突时，以原始仪表趋势、当班日志和复核意见构成证据链。尚未核实的原因标注为“待调查”。
""",
        "manual": f"""## 设备档案

设备位号：{station_code}-{index:02d}-{100 + index}。设备类别：{equipment}。本版维护窗口建议为累计运行 {1800 + index * 25} 小时后进行状态评估。

## 日常检查

检查外观、标识、连接状态和监测信号一致性。趋势值连续 {40 + index * 3 + version} 分钟偏离本站基线时，记录起止时间、数据来源和伴随现象，并提交设备管理员复核。

|现象|优先核对项|记录要求|
|---|---|---|
|信号间歇波动|采集时间与通信状态|保留原始趋势编号|
|显示值冻结|供电与采集链路|记录持续时间|
|现场与远传不一致|仪表标识与时间基准|双人复核|

未经专项方案批准，不得根据本手册实施拆卸、旁路或保护定值调整。
""",
        "procedure": f"""## 职责

当班人员负责发现、记录和报告；值班工程师负责证据复核与影响判断；运行主管决定是否启动专项处置流程。

## 巡检流程

1. 核对设备位号与当班任务单。
2. 查看外观、标识和监测状态，记录观察时间。
3. 将现场观察与控制系统趋势进行一致性比对。
4. 发现异常时生成编号为 {station_code}-XJ-{index:03d} 的巡检记录。
5. 未取得充分证据时，原因字段填写“待调查”。

同类告警在 {40 + index * 3} 分钟内重复出现两次，或现场状态与远传状态持续不一致，应立即报告值班工程师。任何隔离、放空、启停或联锁操作均须进入本站经批准的专项票证流程。
""",
        "case": f"""## 事件概况

{date} 09:{10 + index:02d}，运行人员发现{station}{equipment}远传信号出现短时波动。现场显示稳定，未发现可见泄漏或异常声响。当班人员保留趋势编号 TR-{station_code}-{index:04d} 并通知值班工程师。

## 调查证据

|时间|证据|结论|
|---|---|---|
|09:{10 + index:02d}|控制系统趋势|信号出现三次阶跃|
|09:{18 + index:02d}|现场复核记录|就地显示无同步变化|
|10:{5 + index:02d}|通信日志|存在一次短时重连|

现有证据支持采集链路短时异常，不支持判定设备本体故障。直接原因保持为待进一步验证。责任人：{owner}。关闭条件为连续七日无同类信号异常，并完成趋势与日志联合复核。
""",
        "handover": f"""## 班次信息

日期：2026-{index % 9 + 1:02d}-{index + 7:02d}    班次：白班    接班负责人：{owner}

## 运行摘要

本班输配运行平稳。{equipment}状态标识为“在用”，巡检记录编号 {station_code}-XJ-{index:03d} 已复核。09:{index + 10:02d} 出现一次通信质量提示，现场状态未见同步变化。

|序号|事项|责任人|期限|状态|
|---:|---|---|---|---|
|1|复核通信日志与趋势时间轴|{owner}|次日 10:00|进行中|
|2|补录备件库存批次|林致远|本周五|未开始|

交班人：程越    接班人：{owner}    复核人：韩启明
""",
        "notice": f"""## 通知事项

运行管理部定于 2026-{index % 9 + 1:02d}-15 至 2026-{index % 9 + 1:02d}-18 开展{equipment}专项检查。{station}应在开始前完成设备清单、近期告警记录和未关闭缺陷核对。

1. 使用统一检查表，设备位号不得简写。
2. 每项异常附原始记录编号，不以口头说明代替。
3. 原因未确认的项目统一登记为“待调查”。
4. 汇总表由{owner}复核后于最后一日 17:00 前报送运行管理部。

联系人：陆其安    内部分机：6821    签发人：韩启明
""",
    }[kind]
    return (
        common
        + "\n"
        + sections
        + "\n## 相关记录\n\n本文件关联记录保存于运行档案系统，引用时应同时注明文件编号和版本号。\n"
    )


def _write_pdf(path: Path, title: str, content: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

    font = os.environ.get("KNOWLEDGE_CORPUS_FONT", "/System/Library/Fonts/Supplemental/Songti.ttc")
    pdfmetrics.registerFont(TTFont("CorpusSong", font, subfontIndex=0))
    canvas, (width, height), page, y = (
        Canvas(str(path), pagesize=A4, invariant=1),
        A4,
        1,
        A4[1] - 54,
    )
    for raw in content.splitlines():
        text = raw.replace("#", "").strip()
        if not text:
            y -= 7
            continue
        size = 16 if raw.startswith("# ") else 13 if raw.startswith("#") else 10.5
        for line in textwrap.wrap(text, width=30 if size == 16 else 40 if size == 13 else 52):
            if y < 60:
                canvas.setFont("CorpusSong", 9)
                canvas.drawCentredString(width / 2, 28, f"{title}  第 {page} 页")
                canvas.showPage()
                page += 1
                y = height - 54
            canvas.setFont("CorpusSong", size)
            canvas.drawString(54, y, line)
            y -= size * 1.65
    canvas.setFont("CorpusSong", 9)
    canvas.drawCentredString(width / 2, 28, f"{title}  第 {page} 页")
    canvas.save()


def generate(output: Path, count: int = 60) -> tuple[int, int, int]:
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output directory must be empty; existing data is never overwritten")
    documents, questions, made, version_budget, pdf_budget = [], [], 0, 15, 12
    for kind, maximum, versioned, prefix, label in KINDS:
        for index in range(1, min(maximum, count - made) + 1):
            station, station_code, owner = STATIONS[(made + index) % len(STATIONS)]
            equipment = EQUIPMENT[(made + index * 2) % len(EQUIPMENT)]
            code, title = f"{prefix}-{2025 + index % 2}-{index:03d}", f"{station}{equipment}{label}"
            folder = output / f"{kind}s"
            folder.mkdir(exist_ok=True)
            versions = 2 if index <= versioned and version_budget else 1
            version_budget -= versions == 2
            entries = []
            for version in range(1, versions + 1):
                body = _body(
                    kind, code, title, station, station_code, owner, equipment, index, version
                )
                if kind == "handover" and index % 2 == 0:
                    suffix, mime = ".csv", "text/csv"
                elif count >= 12 and pdf_budget and made % 4 == 0:
                    suffix, mime, pdf_budget = ".pdf", "application/pdf", pdf_budget - 1
                elif made % 3 == 0:
                    suffix, mime = ".txt", "text/plain"
                else:
                    suffix, mime = ".md", "text/markdown"
                path = folder / f"{code}-v{version}{suffix}"
                if suffix == ".pdf":
                    _write_pdf(path, title, body)
                elif suffix == ".csv":
                    rows = [
                        ["记录编号", "站场", "设备", "时间", "状态", "责任人"],
                        [
                            f"{station_code}-JL-{index:04d}",
                            station,
                            equipment,
                            "08:15",
                            "正常",
                            owner,
                        ],
                        [
                            f"{station_code}-JL-{index + 20:04d}",
                            station,
                            equipment,
                            "12:40",
                            "待跟踪",
                            "林致远",
                        ],
                    ]
                    with path.open("w", encoding="utf-8-sig", newline="") as stream:
                        csv.writer(stream).writerows(rows)
                else:
                    path.write_text(body, encoding="utf-8")
                data = path.read_bytes()
                entries.append(
                    {
                        "number": version,
                        "filename": path.name,
                        "path": str(path.relative_to(output)),
                        "mime_type": mime,
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "effective_from": f"202{4 + version}-{index % 9 + 1:02d}-01T00:00:00+08:00",
                        "equipment_models": [f"{station_code}-{index:02d}-{100 + index}"],
                        "authority": "内部",
                    }
                )
            documents.append({"code": code, "title": title, "kind": kind, "versions": entries})
            questions += [
                {
                    "question": f"{code} 当前有效版本的归口单位是什么？",
                    "document_code": code,
                    "version": versions,
                    "should_refuse": False,
                    "split": "held_out" if made % 4 == 0 else "development",
                },
                {
                    "question": f"{code} 能否确定未记录的直接原因？",
                    "document_code": code,
                    "version": versions,
                    "should_refuse": True,
                },
            ]
            made += 1
            if made == count:
                break
        if made == count:
            break
    manifest = {
        "schema_version": 2,
        "corpus_name": "青衡管网生产运行知识库",
        "document_count": len(documents),
        "version_count": sum(len(d["versions"]) for d in documents),
        "documents": documents,
        "questions": questions,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(documents), sum(len(document["versions"]) for document in documents), len(questions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.count <= 60:
        parser.error("count must be between 1 and 60")
    if args.count == 60:
        # The full corpus uses rich PDF/Office layouts. Small counts retain the
        # lightweight deterministic fixture used by unit tests.
        from build_rich_knowledge_corpus import build

        manifest = build(args.output)
        print((manifest["document_count"], manifest["version_count"], len(manifest["questions"])))
    else:
        print(generate(args.output, args.count))
