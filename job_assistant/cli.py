from __future__ import annotations

import argparse
import json
from pathlib import Path

from .exporting import write_results
from .matching import rank_jobs
from .models import Criteria
from .resume import read_resume_file
from .sources import GreenhouseSource, load_jobs_from_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安全、需人工确认的岗位匹配助手")
    parser.add_argument("--resume", required=True, help="简历文件：PDF/DOCX/TXT/MD")
    parser.add_argument("--criteria", required=True, help="筛选条件 JSON")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--board", help="Greenhouse board token 或官方职位页 URL")
    source.add_argument("--jobs", help="离线岗位 JSON，便于演示和测试")
    parser.add_argument("--output", default="output/application_plan.csv")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    criteria_data = json.loads(Path(args.criteria).read_text(encoding="utf-8"))
    criteria = Criteria.from_dict(criteria_data)
    resume_text = read_resume_file(args.resume)
    jobs = (
        GreenhouseSource().fetch(args.board)
        if args.board
        else load_jobs_from_json(args.jobs)
    )
    results = rank_jobs(jobs, resume_text, criteria)
    path = write_results(results, args.output)
    eligible_count = sum(result.eligible for result in results)
    print(f"完成：读取 {len(jobs)} 个岗位，{eligible_count} 个进入人工复核清单。")
    print(f"清单：{path.resolve()}")
    print("程序不会自动提交；请逐项核对后到原招聘页面申请。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
