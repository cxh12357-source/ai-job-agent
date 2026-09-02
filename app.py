from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import streamlit as st

from access_control import lock_current_session, require_access
from config import SETTINGS

from job_assistant.autofill import AutofillError, create_autofill_plan
from job_assistant.autofill_runner import start_autofill_process
from job_assistant.candidate_signals import extract_candidate_signals
from job_assistant.discovery import (
    DEFAULT_BOARD_CATALOG,
    DiscoveryError,
    DiscoveryQuery,
    GreenhouseDiscovery,
)
from job_assistant.exporting import results_to_csv
from job_assistant.matching import rank_jobs
from job_assistant.models import Criteria, MatchResult
from job_assistant.one_click import OneClickApplyError, prepare_and_start_one_click
from job_assistant.profile import (
    ApplicantProfile,
    ProfileError,
    attach_resume,
    choose_resume_language,
    infer_company_foreign,
    load_profile,
    save_profile,
    select_resume,
    store_resume,
)
from job_assistant.resume import ResumeError, extract_resume_text, read_resume_file
from job_assistant.resume_export import ResumeExportError, resume_text_to_docx
from job_assistant.similarity import find_similar_jobs
from job_assistant.sources import GreenhouseSource, SourceError, load_jobs_from_json
from job_assistant.storage import ApplicationRepository
from job_assistant.tailoring import TailoringError, TailoringResult, tailor_resume
from scraper import (
    BOSS_CITY_CODES,
    JobImportError,
    build_official_search_url,
    job_import_template,
    merge_jobs,
    parse_imported_file,
    parse_pasted_jobs,
)

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"
DATABASE = ROOT / "data" / "applications.db"

STATUS_LABELS = {
    "review": "待复核",
    "confirmed": "已确认，待打开",
    "opened": "申请页已打开",
    "blocked": "遇到卡点",
    "submitted": "已投递",
    "offer": "Offer",
    "rejected": "未通过",
    "skipped": "已跳过",
}
FOLLOW_UP_LABELS = {
    "scheduled": "待进行",
    "completed": "已完成",
    "cancelled": "已取消",
}
SALARY_POLICY_LABELS = {
    "保留（推荐，未知薪资不误删）": "include",
    "保留并标记人工核对": "review",
    "排除未公开/不可比较薪资": "exclude",
}
COMPANY_MODE_LABELS = {
    "自动判断（可在岗位详情修正）": "auto",
    "本轮全部按外企处理": "foreign",
    "本轮全部按国内企业处理": "domestic",
}
AUTO_BOARD_OPTIONS = {
    f"{board.company}｜{board.token}｜{' / '.join(board.regions) or '全球'}": board.token
    for board in DEFAULT_BOARD_CATALOG
}
AUTO_DEFAULT_TOKENS = {
    "rzr",
    "quberesearchandtechnologies",
    "appier",
    "peakdesign",
    "casetify",
    "straitsx",
    "xendit",
    "mw-tech-grad",
    "sesai",
}
MARKETPLACE_IMPORT_OPTION = "BOSS直聘 / 猎聘（官网复制导入）"


def comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def infer_resume_text_language(text: str) -> str:
    chinese = len([character for character in text if "\u4e00" <= character <= "\u9fff"])
    latin = len([character for character in text if character.isascii() and character.isalpha()])
    return "zh" if chinese >= max(20, latin // 3) else "en"


def tailoring_base_text(
    profile: ApplicantProfile,
    planned_language: str,
    fallback_text: str,
    fallback_language: str,
) -> tuple[str, str]:
    """Use the matching resume unless an English-target job has a configured English base."""

    if planned_language == "en" and fallback_language != "en":
        path = select_resume(profile, "en", company_foreign=True)
        return read_resume_file(path), "en"
    return fallback_text, fallback_language


def result_label(result: MatchResult) -> str:
    return f"{result.score}分｜{result.job.title}｜{result.job.location}｜{result.job.id}"


def record_label(record: dict[str, object]) -> str:
    return (
        f"{STATUS_LABELS.get(str(record['status']), str(record['status']))}｜"
        f"{record['company']}｜{record['title']}｜{record['job_id']}"
    )


def record_key(record: dict[str, object]) -> tuple[str, str]:
    return str(record["source"]), str(record["job_id"])


def salary_label(record: dict[str, object]) -> str:
    text = str(record.get("salary_text") or "").strip()
    if text:
        return text
    minimum = record.get("salary_min")
    maximum = record.get("salary_max")
    if minimum is None and maximum is None:
        return "未公开"
    currency = str(record.get("currency") or "").strip()
    period = str(record.get("period") or "").strip()
    if minimum is not None and maximum is not None:
        amount = f"{float(minimum):g}–{float(maximum):g}"
    elif minimum is not None:
        amount = f"≥{float(minimum):g}"
    else:
        amount = f"≤{float(maximum):g}"
    return " ".join(part for part in (currency, amount, f"/{period}" if period else "") if part)


def company_is_foreign(job: object, mode: str) -> bool:
    if mode == "foreign":
        return True
    if mode == "domestic":
        return False
    return infer_company_foreign(
        getattr(job, "company", ""),
        job_language=getattr(job, "language", ""),
        title=getattr(job, "title", ""),
        description=getattr(job, "description", ""),
    )


def route_resume_for_result(
    repository: ApplicationRepository,
    profile: ApplicantProfile,
    result: MatchResult,
    company_mode: str,
) -> tuple[str, str, bool, str]:
    current = repository.get(result.job.source, result.job.id) or {}
    existing_path = str(current.get("resume_path") or "").strip()
    existing_language = str(current.get("resume_language") or "").strip()
    if existing_path and existing_language and Path(existing_path).is_file():
        return (
            existing_language,
            existing_path,
            bool(current.get("company_foreign")),
            "",
        )
    foreign = company_is_foreign(result.job, company_mode)
    language = "en" if foreign else choose_resume_language(result.job.language, False)
    try:
        path = select_resume(profile, "en" if foreign else result.job.language, foreign)
    except ProfileError as exc:
        return language, "", foreign, str(exc)
    repository.set_resume_route(
        result.job.source,
        result.job.id,
        language,
        path,
        company_foreign=foreign,
    )
    return language, str(path), foreign, ""


def process_is_running(value: object) -> bool:
    poll = getattr(value, "poll", None)
    if not callable(poll):
        return False
    try:
        return poll() is None
    except OSError:
        return False


def render_one_click_apply(
    repository: ApplicationRepository,
    profile: ApplicantProfile,
    detail: MatchResult,
    *,
    planned_language: str,
    company_foreign: bool,
) -> None:
    source, job_id = detail.job.source, detail.job.id
    record = repository.get(source, job_id) or {}
    status = str(record.get("status") or "review")
    destination = urlsplit(detail.job.url).hostname or "未知域名"
    process_key = f"one-click-process-{source}-{job_id}"
    flash_key = f"one-click-flash-{source}-{job_id}"

    flash = st.session_state.pop(flash_key, None)
    if isinstance(flash, dict):
        st.success(str(flash.get("message") or "官网申请页已打开。"))
        note = str(flash.get("note") or "").strip()
        if note:
            st.caption(note)

    preview_path = ""
    route_error = ""
    existing_path = str(record.get("resume_path") or "").strip()
    existing_language = str(record.get("resume_language") or "").strip()
    if (
        existing_path
        and existing_language == planned_language
        and Path(existing_path).is_file()
    ):
        preview_path = existing_path
    else:
        try:
            preview_path = str(
                select_resume(
                    profile,
                    planned_language,
                    company_foreign=company_foreign or planned_language == "en",
                )
            )
        except ProfileError as exc:
            route_error = str(exc)

    st.markdown("### 一键投递")
    resume_label = Path(preview_path).name if preview_path else "尚未配置"
    st.info(
        f"{detail.job.company} · {detail.job.title}｜{destination}｜"
        f"{'英文' if planned_language == 'en' else '中文'}简历：{resume_label}"
    )
    st.caption(
        "点击即授权本次只向上述岗位填写姓名、邮箱、电话、所在地并上传所示简历。"
        "程序会自动生成只使用原有事实的岗位版简历；验证码、签证、薪资和其他特殊问题"
        "会留给你处理，官网最后的 Submit 仍由你亲自点击。"
    )

    if SETTINGS.cloud_deployment:
        st.warning(
            "云端安全版不能控制你电脑上的浏览器，也不会向招聘网站发送个人资料。"
            "请打开官网手动投递；一键预填功能仍可在本机版使用。"
        )
        st.link_button("打开岗位官网", detail.job.url, width="stretch")
        return

    if route_error:
        st.warning(route_error + "。请先到“申请资料”补充后再点一键投递。")
    if not source.casefold().startswith("greenhouse"):
        st.warning("这个岗位暂不支持一键填写，请使用原岗位页手动投递。")
    if not detail.eligible:
        st.warning("这个岗位未达到当前筛选条件；放宽条件并重新匹配后才可一键投递。")
    if status in {"submitted", "offer", "rejected", "skipped"}:
        st.info(f"该岗位当前状态：{STATUS_LABELS.get(status, status)}。")

    active_process = process_is_running(st.session_state.get(process_key))
    if active_process:
        st.success("这个岗位的官网申请窗口已经打开，请在窗口中完成最后确认。")

    disabled = bool(
        route_error
        or not source.casefold().startswith("greenhouse")
        or not detail.eligible
        or status in {"submitted", "offer", "rejected", "skipped"}
        or active_process
    )
    button_label = "重新打开并自动填写" if status == "opened" else "一键投递这个岗位"
    if st.button(
        button_label,
        type="primary",
        key=f"one-click-apply-{source}-{job_id}",
        disabled=disabled,
        width="stretch",
    ):
        try:
            with st.spinner("正在准备合适的简历并打开官网……"):
                outcome = prepare_and_start_one_click(
                    repository,
                    profile,
                    detail,
                    planned_language=planned_language,
                    company_foreign=company_foreign,
                )
            st.session_state[process_key] = outcome.process
            st.session_state[f"autofill-pid-{source}-{job_id}"] = outcome.process.pid
            st.session_state[flash_key] = {
                "message": (
                    "官网窗口已打开，基本资料和简历会自动填写。"
                    "请核对特殊问题，并在官网亲自点击最后的提交按钮。"
                ),
                "note": outcome.note,
            }
            st.rerun()
        except (
            AutofillError,
            OneClickApplyError,
            ProfileError,
            ResumeError,
            ResumeExportError,
            TailoringError,
            OSError,
            ValueError,
            KeyError,
        ) as exc:
            st.error(f"暂未打开官网，也没有提交申请：{exc}")


def render_profile_tab() -> None:
    st.subheader("本地申请资料与双语简历")
    st.info(
        "这些资料只保存在这台电脑。只有你在某个已确认岗位上再次授权后，"
        "程序才会把下列五项和选中的简历填写到该公司的 HTTPS 申请页。"
    )
    try:
        profile = load_profile()
    except ProfileError as exc:
        st.error(str(exc))
        profile = ApplicantProfile()

    with st.form("profile-form"):
        name_cols = st.columns(2)
        first_name = name_cols[0].text_input(
            "名（First Name）", value=profile.first_name, placeholder="例如：Xiaoming"
        )
        last_name = name_cols[1].text_input(
            "姓（Last Name）", value=profile.last_name, placeholder="例如：Chen"
        )
        contact_cols = st.columns(3)
        email = contact_cols[0].text_input("邮箱", value=profile.email)
        phone = contact_cols[1].text_input("电话", value=profile.phone)
        location = contact_cols[2].text_input(
            "当前所在地", value=profile.location, placeholder="例如：Shanghai, China"
        )
        resume_cols = st.columns(2)
        chinese_upload = resume_cols[0].file_uploader(
            "中文版简历（PDF / DOCX）", type=["pdf", "docx"], key="profile-zh-resume"
        )
        english_upload = resume_cols[1].file_uploader(
            "英文版简历（PDF / DOCX）", type=["pdf", "docx"], key="profile-en-resume"
        )
        st.caption("隐私存储限制：每份申请简历最大 10 MB；文件类型和内容签名都会校验。")
        submitted = st.form_submit_button("保存到本机", type="primary", width="stretch")

    if submitted:
        updated = ApplicantProfile(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            location=location,
            chinese_resume=profile.chinese_resume,
            english_resume=profile.english_resume,
            application_fields=profile.application_fields,
        )
        try:
            if chinese_upload is not None:
                updated, _ = attach_resume(
                    updated, chinese_upload.name, chinese_upload.getvalue(), "zh"
                )
            if english_upload is not None:
                updated, _ = attach_resume(
                    updated, english_upload.name, english_upload.getvalue(), "en"
                )
            save_profile(updated)
            st.success("申请资料已安全保存在本机。")
            profile = updated
        except ProfileError as exc:
            st.error(str(exc))

    status_cols = st.columns(3)
    required_values = (
        profile.first_name,
        profile.last_name,
        profile.email,
        profile.phone,
        profile.location,
    )
    status_cols[0].metric("基础资料", "完整" if all(required_values) else "待补充")
    status_cols[1].metric("中文版简历", profile.chinese_resume or "未上传")
    status_cols[2].metric("英文版简历", profile.english_resume or "未上传")
    st.caption(
        "外企岗位默认绑定英文版简历；国内企业按岗位语言选择。"
        "护照号、身份证号、签证/工作资格、期望薪资等不会被自动填写。"
    )


def render_tailoring_section(
    repository: ApplicationRepository,
    profile: ApplicantProfile,
    detail: MatchResult,
    *,
    planned_language: str,
    company_foreign: bool,
) -> None:
    st.markdown("#### 根据当前 JD 生成岗位定制简历")
    st.caption(
        "只会重排、突出和规范原简历中已有的事实；缺失技能单独提示，"
        "不会为了提高分数虚构经历。这里可先人工编辑；一键投递只自动采用未经手改的安全草稿。"
    )
    fallback_text = str(st.session_state.get("resume_text") or "")
    fallback_language = str(st.session_state.get("resume_input_language") or "zh")
    if not fallback_text:
        st.info("请先运行一次岗位匹配，程序才能读取本轮简历正文。")
        return

    base_text = ""
    base_language = fallback_language
    base_error = ""
    try:
        base_text, base_language = tailoring_base_text(
            profile,
            planned_language,
            fallback_text,
            fallback_language,
        )
    except (ProfileError, ResumeError, OSError) as exc:
        base_error = str(exc)

    if base_error:
        st.warning(
            "这个岗位计划使用英文简历，但申请资料中没有可读取的英文版。"
            "请先到“申请资料”上传英文简历，再生成该岗位的定制版。"
        )
        st.caption(base_error)
        return

    draft_key = (detail.job.source, detail.job.id)
    edit_key = f"tailored-text-{detail.job.source}-{detail.job.id}"
    drafts: dict[tuple[str, str], TailoringResult] = st.session_state.setdefault(
        "tailoring_drafts", {}
    )
    if draft_key not in drafts:
        if st.button(
            "生成不虚构的定制草稿",
            key=f"generate-tailored-{detail.job.source}-{detail.job.id}",
            width="stretch",
        ):
            try:
                draft = tailor_resume(
                    base_text,
                    detail.job.description,
                    job_title=detail.job.title,
                    company=detail.job.company,
                )
                drafts[draft_key] = draft
                st.session_state[edit_key] = draft.tailored_text
                st.rerun()
            except TailoringError as exc:
                st.error(str(exc))
        return

    draft = drafts[draft_key]
    coverage_cols = st.columns(3)
    coverage_cols[0].metric("JD 关键词覆盖", f"{draft.coverage_ratio:.0%}")
    coverage_cols[1].metric("已有证据", len(draft.covered_keywords))
    coverage_cols[2].metric("需要补强/核对", len(draft.missing_keywords))
    if draft.covered_keywords:
        st.success("原简历已有证据：" + "、".join(draft.covered_keywords))
    if draft.gaps:
        with st.expander("缺口提示（不会自动写入简历）", expanded=True):
            for gap in draft.gaps:
                st.write(f"- {gap}")
    with st.expander("本次改动与资格风险"):
        for change in draft.changes:
            st.write(f"- {change.detail}")
        for warning in draft.warnings:
            st.write(f"- {warning}")

    if edit_key not in st.session_state:
        st.session_state[edit_key] = draft.tailored_text
    edited_text = st.text_area(
        "定制后的简历正文（可在批准前人工修正）",
        key=edit_key,
        height=520,
        help="如果手动增加内容，请确保每一项都真实、可证明。",
    )
    try:
        docx_payload = resume_text_to_docx(edited_text)
    except ResumeExportError as exc:
        st.error(str(exc))
        docx_payload = b""

    download_cols = st.columns(3)
    download_cols[0].download_button(
        "下载改动报告",
        data=draft.to_markdown(),
        file_name=f"tailoring_report_{detail.job.id}.md",
        mime="text/markdown",
        width="stretch",
    )
    download_cols[1].download_button(
        "下载定制简历 DOCX",
        data=docx_payload,
        file_name=f"tailored_resume_{detail.job.id}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        disabled=not bool(docx_payload),
        width="stretch",
    )
    if download_cols[2].button(
        "重新生成草稿",
        key=f"reset-tailored-{detail.job.source}-{detail.job.id}",
        width="stretch",
    ):
        drafts.pop(draft_key, None)
        st.session_state.pop(edit_key, None)
        st.rerun()

    approval = st.checkbox(
        "我已核对定制版只包含真实、可证明的经历，并同意将它绑定到这个岗位",
        key=f"approve-tailored-ack-{detail.job.source}-{detail.job.id}",
    )
    if st.button(
        "批准并绑定此岗位定制版",
        type="primary",
        key=f"approve-tailored-{detail.job.source}-{detail.job.id}",
        width="stretch",
    ):
        if not approval:
            st.error("请先完成真实性核对并勾选确认。")
        elif not docx_payload:
            st.error("定制简历尚未成功生成。")
        else:
            try:
                stored = store_resume(
                    f"{detail.job.company}_{detail.job.title}_tailored.docx",
                    docx_payload,
                    base_language,
                )
                repository.approve_tailored_resume(
                    detail.job.source,
                    detail.job.id,
                    base_language,
                    stored,
                    changes="；".join(change.detail for change in draft.changes),
                    gaps="；".join(draft.gaps),
                    company_foreign=company_foreign,
                )
                st.success(
                    "已批准并绑定岗位定制版；原始中/英文简历没有被覆盖。"
                    "一键投递时，官网填写会上传这份定制版。"
                )
            except (ProfileError, ValueError, KeyError, OSError) as exc:
                st.error(str(exc))


def render_match_tab(repository: ApplicationRepository) -> None:
    try:
        profile = load_profile()
    except ProfileError as exc:
        profile = ApplicantProfile()
        st.error(f"申请资料读取失败：{exc}")

    st.subheader("1. 选择用于匹配的简历")
    resume_mode = st.radio(
        "匹配简历",
        ["内置示例（首次运行推荐）", "申请资料中的中文简历", "申请资料中的英文简历", "临时上传"],
        horizontal=True,
    )
    uploaded = None
    if resume_mode == "临时上传":
        uploaded = st.file_uploader("选择简历", type=["pdf", "docx", "txt", "md"])
    elif "申请资料" in resume_mode:
        filename = profile.english_resume if "英文" in resume_mode else profile.chinese_resume
        st.caption(f"当前文件：{filename or '尚未配置，请先到“申请资料”页上传'}")

    st.subheader("2. 选择岗位来源")
    source_mode = st.radio(
        "来源",
        [
            "自动搜索多个 Greenhouse 公司（推荐）",
            "指定一个 Greenhouse 职位页",
            MARKETPLACE_IMPORT_OPTION,
            "离线示例（首次运行推荐）",
        ],
        horizontal=True,
    )
    board_value = ""
    selected_board_tokens: list[str] = []
    use_resume_signals = True
    marketplace_platform = "BOSS直聘"
    marketplace_text = ""
    marketplace_files: list[object] = []
    if source_mode.startswith("自动搜索"):
        default_labels = [
            label for label, token in AUTO_BOARD_OPTIONS.items() if token in AUTO_DEFAULT_TOKENS
        ]
        selected_board_labels = st.multiselect(
            "选择要搜索的公司（已限制为经过审核的公开 Greenhouse board）",
            list(AUTO_BOARD_OPTIONS),
            default=default_labels,
        )
        selected_board_tokens = [AUTO_BOARD_OPTIONS[label] for label in selected_board_labels]
        use_resume_signals = st.checkbox(
            "使用简历中明确出现的技能提高搜索相关性",
            value=True,
            help="技能只在本机提取；Greenhouse 只收到公开职位读取请求。",
        )
        st.caption(
            "Greenhouse 没有全网职位搜索接口，因此本功能会温和地扫描你启用的公司目录；"
            "结果缓存 6 小时，单个公司失败不会中断整轮搜索。"
        )
    elif source_mode == "指定一个 Greenhouse 职位页":
        board_value = st.text_input(
            "Greenhouse 职位页或 board token",
            placeholder="例如：https://job-boards.greenhouse.io/companyname",
        )
        st.caption("只读取 Greenhouse 官方公开接口；匹配时不会发送你的简历或个人资料。")
    elif source_mode == MARKETPLACE_IMPORT_OPTION:
        marketplace_platform = st.radio(
            "招聘平台",
            ["BOSS直聘", "猎聘"],
            horizontal=True,
        )
        search_columns = st.columns([2, 1, 1])
        marketplace_keyword = search_columns[0].text_input(
            "先在官网搜索",
            "应届生 工程师",
            help="这里只生成官方搜索入口，App 不会后台抓取或接管你的登录状态。",
        )
        marketplace_city = search_columns[1].selectbox(
            "城市",
            list(BOSS_CITY_CODES) if marketplace_platform == "BOSS直聘" else ["官网选择"],
        )
        try:
            marketplace_search_url = build_official_search_url(
                marketplace_platform,
                marketplace_keyword,
                city=marketplace_city if marketplace_platform == "BOSS直聘" else "上海",
            )
        except JobImportError:
            marketplace_search_url = (
                "https://www.zhipin.com/"
                if marketplace_platform == "BOSS直聘"
                else "https://www.liepin.com/"
            )
        search_columns[2].link_button(
            f"打开{marketplace_platform}官网",
            marketplace_search_url,
            width="stretch",
        )
        st.info(
            "请在官网正常登录并打开你感兴趣的单个岗位，然后复制真实岗位信息。"
            "本工具会在本机解析、匹配、去重；不会隐藏自动化标记、模拟真人或绕过验证码。"
        )
        paste_tab, file_tab = st.tabs(["粘贴岗位", "上传已保存的岗位文件"])
        with paste_tab:
            marketplace_text = st.text_area(
                "岗位信息",
                placeholder=job_import_template(marketplace_platform),
                height=280,
                help="必填：岗位名称、公司名称、工作地点、岗位详情链接和完整职位描述。"
                "多个岗位用 ===JOB=== 分隔。",
            )
        with file_tab:
            marketplace_files = st.file_uploader(
                "选择 JSON / HTML / TXT 文件",
                type=["json", "html", "htm", "txt", "md"],
                accept_multiple_files=True,
                help="HTML 必须是你本人在浏览器中保存的岗位详情页；"
                "JSON/TXT 可使用下载模板整理多个岗位。",
            )
            st.download_button(
                "下载纯文本导入模板",
                data=job_import_template(marketplace_platform),
                file_name=f"{marketplace_platform}-岗位导入模板.txt",
                mime="text/plain",
            )

    company_mode_label = st.selectbox(
        "公司类型与简历语言",
        list(COMPANY_MODE_LABELS),
        help="自动模式依据岗位语言和 JD 语言做保守判断；外企默认使用英文简历，可逐岗位修正。",
    )

    st.subheader("3. 自主设置目标岗位、地点与薪资")
    left, middle, right = st.columns(3)
    with left:
        target_titles = st.text_input(
            "目标岗位（逗号分隔）",
            "热管理工程师, 机械工程师, 制造工程师, 工艺工程师, 测试工程师, "
            "数据分析实习生, AI应用, Infrastructure, Cloud, Technical Solution, "
            "Account Operations",
        )
        locations = st.text_input(
            "就业地点（逗号分隔，留空则不限）",
            "上海, 北京, 深圳, 东莞, 苏州, 香港, 新加坡",
        )
    with middle:
        required_keywords = st.text_input("必须包含（不确定时建议留空）", "")
        preferred_keywords = st.text_input(
            "加分关键词",
            "Python, Excel, 数据分析, AI Agent, LLM, 自动化, SolidWorks, AutoCAD, 热管理",
        )
    with right:
        excluded_keywords = st.text_input("排除关键词", "Senior, Director, 5+ years")
        minimum_score = st.slider("最低匹配分", 0, 100, 60)
        max_results = st.number_input("清单最多显示", 1, 100, 20)

    salary_enabled = st.checkbox("启用目标薪资筛选")
    expected_salary_min: float | None = None
    expected_salary_max: float | None = None
    salary_currency = ""
    salary_period = ""
    unknown_salary_policy = "include"
    if salary_enabled:
        salary_cols = st.columns(5)
        minimum_value = salary_cols[0].number_input(
            "最低期望", min_value=0.0, value=15000.0, step=1000.0
        )
        maximum_value = salary_cols[1].number_input(
            "最高目标（0=不限）", min_value=0.0, value=0.0, step=1000.0
        )
        salary_currency = salary_cols[2].selectbox("币种", ["CNY", "USD", "EUR", "GBP"])
        period_label = salary_cols[3].selectbox("计薪周期", ["月薪", "年薪"])
        policy_label = salary_cols[4].selectbox("岗位未公开薪资时", list(SALARY_POLICY_LABELS))
        expected_salary_min = minimum_value
        expected_salary_max = maximum_value or None
        salary_period = "month" if period_label == "月薪" else "year"
        unknown_salary_policy = SALARY_POLICY_LABELS[policy_label]

    if source_mode.startswith("自动搜索"):
        search_button_label = "自动搜索并匹配岗位"
    elif source_mode == MARKETPLACE_IMPORT_OPTION:
        search_button_label = "导入并匹配真实岗位"
    else:
        search_button_label = "开始匹配"
    if st.button(search_button_label, type="primary", width="stretch"):
        st.session_state.pop("results", None)
        st.session_state.pop("jobs", None)
        st.session_state.pop("resume_routes", None)
        st.session_state.pop("confirmed_keys", None)
        st.session_state.pop("discovery_report", None)
        st.session_state.pop("tailoring_drafts", None)
        try:
            if resume_mode.startswith("内置"):
                resume_path = EXAMPLES / "resume.txt"
                resume_bytes = resume_path.read_bytes()
                resume_name = resume_path.name
            elif resume_mode == "临时上传":
                if uploaded is None:
                    raise ResumeError("请先上传简历")
                resume_bytes = uploaded.getvalue()
                resume_name = uploaded.name
            else:
                wanted_language = "en" if "英文" in resume_mode else "zh"
                resume_path = select_resume(
                    profile,
                    wanted_language,
                    company_foreign=wanted_language == "en",
                )
                resume_bytes = resume_path.read_bytes()
                resume_name = resume_path.name

            resume_text = extract_resume_text(resume_name, resume_bytes)
            resume_input_language = infer_resume_text_language(resume_text)
            candidate_signals = extract_candidate_signals(resume_text)
            criteria = Criteria.from_dict(
                {
                    "target_titles": comma_list(target_titles),
                    "locations": comma_list(locations),
                    "required_keywords": comma_list(required_keywords),
                    "preferred_keywords": comma_list(preferred_keywords),
                    "excluded_keywords": comma_list(excluded_keywords),
                    "minimum_score": minimum_score,
                    "max_results": int(max_results),
                    "expected_salary_min": expected_salary_min,
                    "expected_salary_max": expected_salary_max,
                    "salary_currency": salary_currency,
                    "salary_period": salary_period,
                    "unknown_salary_policy": unknown_salary_policy,
                }
            )
            if source_mode.startswith("离线"):
                jobs = load_jobs_from_json(EXAMPLES / "jobs.json")
            elif source_mode == "指定一个 Greenhouse 职位页":
                if not board_value.strip():
                    raise SourceError("请输入 Greenhouse 职位页或 board token")
                jobs = GreenhouseSource().fetch(board_value)
            elif source_mode == MARKETPLACE_IMPORT_OPTION:
                imported_groups = []
                if marketplace_text.strip():
                    imported_groups.append(
                        parse_pasted_jobs(marketplace_text, marketplace_platform)
                    )
                for imported_file in marketplace_files:
                    imported_groups.append(
                        parse_imported_file(
                            imported_file.name,
                            imported_file.getvalue(),
                            marketplace_platform,
                        )
                    )
                if not imported_groups:
                    raise JobImportError(
                        "请先从官网复制至少一个岗位，或上传本人保存的岗位文件"
                    )
                with st.spinner("正在本机解析完整 JD、匹配简历并检查重复岗位……"):
                    jobs = merge_jobs(*imported_groups)
            else:
                if not selected_board_tokens:
                    raise DiscoveryError("请至少选择一个要搜索的 Greenhouse 公司")
                discovery_keywords = (
                    candidate_signals.keywords
                    if use_resume_signals
                    else tuple(comma_list(preferred_keywords))
                )
                query = DiscoveryQuery(
                    resume_keywords=discovery_keywords,
                    locations=criteria.locations,
                    target_titles=criteria.target_titles or candidate_signals.suggested_titles,
                    # 标题和地点仍由完整评分器严格判断；关键词在粗筛阶段只用于排序，
                    # 避免中英文表达差异把真实相关岗位提前删掉。
                    minimum_keyword_hits=0,
                    limit=min(500, max(100, int(max_results) * 8)),
                )
                with st.spinner("正在读取公开 Greenhouse 职位并在本机匹配简历……"):
                    report = GreenhouseDiscovery().discover(
                        query,
                        board_tokens=selected_board_tokens,
                    )
                jobs = list(report.jobs)
                st.session_state["discovery_report"] = report
                if not jobs:
                    raise SourceError(
                        "本轮没有找到同时符合岗位和地点粗筛的职位；请放宽岗位名称或地点。"
                    )

            results = rank_jobs(jobs, resume_text, criteria)
            repository.save_results(results)
            company_mode = COMPANY_MODE_LABELS[company_mode_label]
            routes: dict[tuple[str, str], dict[str, object]] = {}
            for result in results:
                language, path, foreign, error = route_resume_for_result(
                    repository, profile, result, company_mode
                )
                routes[(result.job.source, result.job.id)] = {
                    "language": language,
                    "path": path,
                    "foreign": foreign,
                    "error": error,
                }
            st.session_state["results"] = results
            st.session_state["jobs"] = jobs
            st.session_state["resume_routes"] = routes
            st.session_state["company_mode"] = company_mode
            st.session_state["resume_text"] = resume_text
            st.session_state["resume_input_language"] = resume_input_language
            st.session_state["candidate_signals"] = candidate_signals
            st.success(
                f"已读取 {len(jobs)} 个岗位；"
                f"{sum(item.eligible for item in results)} 个进入人工复核清单。"
            )
            missing_routes = sum(bool(route["error"]) for route in routes.values())
            if missing_routes:
                st.warning(f"有 {missing_routes} 个岗位尚未绑定所需简历，请到“申请资料”页补充。")
        except (
            ResumeError,
            ProfileError,
            SourceError,
            DiscoveryError,
            JobImportError,
            ValueError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            st.error(str(exc))

    results: list[MatchResult] = st.session_state.get("results", [])
    jobs = st.session_state.get("jobs", [])
    routes = st.session_state.get("resume_routes", {})
    if not results:
        if source_mode == MARKETPLACE_IMPORT_OPTION:
            st.info("从官网复制岗位信息后，点击“导入并匹配真实岗位”。")
        else:
            st.info("上传或选择简历后，点击岗位匹配按钮。")
        return

    st.subheader("4. 查看岗位清单")
    signals = st.session_state.get("candidate_signals")
    if signals and signals.keywords:
        st.caption(
            "本机从简历识别的找岗信号："
            + "、".join(signals.keywords)
            + "。这些内容没有发送给 Greenhouse。"
        )
    discovery_report = st.session_state.get("discovery_report")
    if discovery_report is not None:
        discovery_cols = st.columns(4)
        discovery_cols[0].metric("扫描公司", len(discovery_report.boards_queried))
        discovery_cols[1].metric("读取公开岗位", discovery_report.total_jobs_seen)
        discovery_cols[2].metric("粗筛后岗位", len(discovery_report.hits))
        discovery_cols[3].metric("使用缓存", len(discovery_report.cache_hits))
        if discovery_report.failures:
            with st.expander("部分公司读取失败（其余结果仍可正常使用）"):
                for failure in discovery_report.failures:
                    cache_note = "，已使用旧缓存" if failure.used_stale_cache else ""
                    st.write(f"- {failure.company}（{failure.token}）：{failure.message}{cache_note}")
    table_rows: list[dict[str, object]] = []
    for item in results:
        route = routes.get((item.job.source, item.job.id), {})
        stored_record = repository.get(item.job.source, item.job.id) or {}
        language = str(stored_record.get("resume_language") or route.get("language") or "")
        tailored = bool(stored_record.get("resume_tailored"))
        table_rows.append(
            {
                "岗位": item.job.title,
                "公司": item.job.company,
                "地点": item.job.location,
                "薪资": item.job.salary_text or "未公开",
                "匹配度": item.score,
                "薪资符合": item.salary_eligible,
                "简历": (
                    f"岗位定制版（{'英文' if language == 'en' else '中文'}）"
                    if tailored
                    else "英文版" if language == "en" else "中文版"
                ),
                "进入复核": item.eligible,
                "原岗位页": item.job.url,
            }
        )
    st.dataframe(
        table_rows,
        column_config={
            "原岗位页": st.column_config.LinkColumn("原岗位页"),
            "进入复核": st.column_config.CheckboxColumn("进入复核"),
            "薪资符合": st.column_config.CheckboxColumn("薪资符合"),
            "匹配度": st.column_config.ProgressColumn("匹配度", min_value=0, max_value=100),
        },
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "下载安全 CSV 清单",
        data="\ufeff" + results_to_csv(results),
        file_name=f"application_plan_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
    )

    detail_options = {result_label(item): item for item in results}
    detail = detail_options[st.selectbox("选择岗位查看完整 JD 和同类型岗位", list(detail_options))]
    detail_record = repository.get(detail.job.source, detail.job.id) or {}
    metric_cols = st.columns(3)
    metric_cols[0].metric("匹配度", f"{detail.score}%")
    metric_cols[1].metric("岗位薪资", salary_label(detail_record))
    route_language = str(detail_record.get("resume_language") or "")
    route_tailored = bool(detail_record.get("resume_tailored"))
    metric_cols[2].metric(
        "计划简历",
        (
            f"岗位定制版（{'英文' if route_language == 'en' else '中文'}）"
            if route_tailored
            else "英文版" if route_language == "en"
            else "中文版" if route_language == "zh"
            else "未配置"
        ),
    )
    with st.expander("完整岗位 JD", expanded=True):
        st.write(detail.job.description or "该岗位源未提供 JD 正文。")
    with st.expander("匹配理由与薪资判断"):
        for reason in detail.reasons:
            st.write(f"- {reason}")
        if detail.salary_reason:
            st.write(f"- {detail.salary_reason}")

    current_mode = str(st.session_state.get("company_mode", "auto"))
    default_foreign = bool(detail_record.get("company_foreign")) or company_is_foreign(
        detail.job, current_mode
    )
    with st.expander("高级设置（可选：切换简历或先查看定制版）"):
        foreign_override = st.checkbox(
            "此岗位按外企处理（自动使用英文简历）",
            value=default_foreign,
            key=f"foreign-{detail.job.source}-{detail.job.id}",
        )
        planned_language = "en" if foreign_override else choose_resume_language(
            detail.job.language, False
        )
        st.caption(
            f"当前规则将使用：{'英文版' if planned_language == 'en' else '中文版'}简历"
        )
        if st.button("保存此岗位的简历选择"):
            try:
                path = select_resume(
                    profile,
                    "en" if foreign_override else detail.job.language,
                    company_foreign=foreign_override,
                )
                repository.set_resume_route(
                    detail.job.source,
                    detail.job.id,
                    planned_language,
                    path,
                    company_foreign=foreign_override,
                )
                st.success(
                    f"已绑定{'英文版' if planned_language == 'en' else '中文版'}简历。"
                )
                st.rerun()
            except ProfileError as exc:
                st.error(str(exc))

        render_tailoring_section(
            repository,
            profile,
            detail,
            planned_language=planned_language,
            company_foreign=foreign_override,
        )

    render_one_click_apply(
        repository,
        profile,
        detail,
        planned_language=planned_language,
        company_foreign=foreign_override,
    )

    st.markdown("#### 同类型其他岗位")
    similar = find_similar_jobs(detail.job, jobs, limit=5, minimum_score=20)
    if similar:
        st.dataframe(
            [
                {
                    "岗位": item.job.title,
                    "公司": item.job.company,
                    "地点": item.job.location,
                    "相似度": item.score,
                    "为什么相似": "；".join(item.reasons),
                    "原岗位页": item.job.url,
                }
                for item in similar
            ],
            column_config={
                "相似度": st.column_config.ProgressColumn("相似度", min_value=0, max_value=100),
                "原岗位页": st.column_config.LinkColumn("原岗位页"),
            },
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("当前读取的岗位池中没有足够相似的其他岗位。")

    if not any(item.eligible for item in results):
        st.warning("本轮没有岗位达到复核条件。")


def render_autofill_card(
    repository: ApplicationRepository,
    record: dict[str, object],
) -> None:
    source, job_id = record_key(record)
    if SETTINGS.cloud_deployment:
        st.caption("云端安全版不启动浏览器自动填写；可打开原岗位页手动完成申请。")
        job_url = str(record.get("url") or "")
        if job_url.startswith("https://"):
            st.link_button("打开岗位官网", job_url, width="stretch")
        return
    if not source.casefold().startswith("greenhouse"):
        st.caption("当前一键官网填写仅支持 Greenhouse 来源岗位；其他网站请使用原岗位页。")
        return

    try:
        profile = load_profile()
    except ProfileError as exc:
        st.error(f"申请资料不可用：{exc}")
        return

    resume_path = str(record.get("resume_path") or "").strip()
    route_was_missing = not bool(resume_path)
    resume_language = str(record.get("resume_language") or "").strip()
    company_foreign = bool(record.get("company_foreign"))
    if not resume_path:
        company_foreign = company_foreign or infer_company_foreign(
            str(record.get("company") or ""),
            job_language=str(record.get("job_language") or ""),
            title=str(record.get("title") or ""),
            description=str(record.get("description") or ""),
        )
        resume_language = (
            "en"
            if company_foreign
            else choose_resume_language(str(record.get("job_language") or ""), False)
        )
        try:
            candidate = select_resume(
                profile,
                "en" if company_foreign else str(record.get("job_language") or ""),
                company_foreign=company_foreign,
            )
            resume_path = str(candidate)
        except ProfileError as exc:
            st.warning(f"暂不能自动填写：{exc}")
            return

    destination = urlsplit(str(record["url"])).hostname or "未知域名"
    st.markdown("##### 重新打开官网并自动填写")
    st.info(
        f"本次目的地：{destination}｜简历：{Path(resume_path).name}"
        f"（{'岗位定制版，' if record.get('resume_tailored') else ''}"
        f"{'英文' if resume_language == 'en' else '中文'}）。"
    )
    st.caption(
        "点击下方按钮即授权本次向上述岗位填写名、姓、邮箱、电话、当前所在地并上传简历。"
        "不会回答签证、工作资格、薪资或其他自定义问题，也不会点击最终提交。"
    )
    button_label = "一键打开并自动填写" if record["status"] == "confirmed" else "重新打开并自动填写"
    if st.button(
        button_label,
        type="primary",
        key=f"autofill-start-{source}-{job_id}",
        width="stretch",
    ):
        try:
            plan = create_autofill_plan(
                str(record["url"]),
                profile,
                resume_path,
                source=source,
                consent=True,
                dry_run=False,
            )
            process = start_autofill_process(plan)
            if route_was_missing:
                repository.set_resume_route(
                    source,
                    job_id,
                    resume_language,
                    resume_path,
                    company_foreign=company_foreign,
                )
            if record["status"] == "confirmed":
                repository.update_status(source, job_id, "opened")
            repository.update_details(
                source,
                job_id,
                notes=str(record.get("notes") or ""),
                next_action="在可见官网申请页核对、回答额外问题并由本人提交",
                current_stage="官网表单已启动，待人工核对",
            )
            st.session_state[f"autofill-pid-{source}-{job_id}"] = process.pid
            st.success(
                "已启动独立浏览器。安全字段会自动填写；若出现验证码、登录或额外必填题，"
                "程序会停止自动操作并交给你处理。最终提交必须由你亲自点击。"
            )
        except (AutofillError, ProfileError, OSError, ValueError) as exc:
            st.error(str(exc))


def render_tracking_tab(repository: ApplicationRepository) -> None:
    records = repository.list_all()
    if not records:
        st.info("还没有岗位记录，请先完成一次匹配。")
        return

    counts = {status: sum(row["status"] == status for row in records) for status in STATUS_LABELS}
    metric_cols = st.columns(4)
    metric_cols[0].metric("待复核", counts["review"])
    metric_cols[1].metric(
        "申请进行中", counts["confirmed"] + counts["opened"] + counts["blocked"]
    )
    metric_cols[2].metric("已投递", counts["submitted"])
    metric_cols[3].metric("已结束", counts["offer"] + counts["rejected"])

    filter_options = ["全部", *STATUS_LABELS.values()]
    selected_filter = st.selectbox("按状态筛选", filter_options)
    visible = records
    if selected_filter != "全部":
        wanted = next(key for key, label in STATUS_LABELS.items() if label == selected_filter)
        visible = [record for record in records if record["status"] == wanted]

    st.dataframe(
        [
            {
                "状态": STATUS_LABELS.get(str(row["status"]), row["status"]),
                "岗位": row["title"],
                "公司": row["company"],
                "地点": row["location"],
                "匹配分": row["score"],
                "当前阶段": row["current_stage"],
                "下一步": row["next_action"],
                "原岗位页": row["url"],
            }
            for row in visible
        ],
        column_config={"原岗位页": st.column_config.LinkColumn("原岗位页")},
        hide_index=True,
        width="stretch",
    )

    if not visible:
        st.info("该状态下暂无岗位。")
        return

    options = {record_label(row): row for row in visible}
    selected_label = st.selectbox("选择一条记录管理", list(options))
    record = options[selected_label]
    source, job_id = record_key(record)
    status = str(record["status"])

    st.markdown(f"#### {record['company']} · {record['title']}")
    st.caption(
        f"{record['location']}｜{record['score']} 分｜{salary_label(record)}｜"
        f"{STATUS_LABELS.get(status, status)}"
    )
    resume_language = str(record.get("resume_language") or "")
    if resume_language:
        st.write(
            f"计划简历：{'岗位定制版' if record.get('resume_tailored') else '基准版'}"
            f"（{'英文' if resume_language == 'en' else '中文'}）"
            f"（{Path(str(record.get('resume_path') or '')).name or '文件待补充'}）"
        )
    if record.get("resume_tailored"):
        with st.expander("查看定制简历的改动与缺口"):
            st.write(record.get("tailoring_summary") or "未记录自动改动")
            if record.get("tailoring_gaps"):
                st.warning(str(record["tailoring_gaps"]))
    with st.expander("查看完整岗位 JD"):
        st.write(record.get("description") or "该岗位记录暂无 JD 正文，请查看原岗位页。")
    with st.expander("查看匹配理由"):
        st.write(record["reasons"] or "暂无")
    st.link_button("打开原岗位页", str(record["url"]))

    if status in {"confirmed", "opened"}:
        render_autofill_card(repository, record)

    if status == "confirmed":
        if st.button("标记为：申请页已打开", type="primary"):
            repository.update_status(source, job_id, "opened")
            st.rerun()
    elif status == "opened":
        st.markdown("##### 提交结果登记")
        evidence = st.text_input(
            "提交证据",
            placeholder="例如：感谢页文字、确认编号或确认邮件主题",
            key=f"evidence-{source}-{job_id}",
        )
        submitted_ack = st.checkbox(
            "我已在原招聘网站亲自完成最终提交",
            key=f"submitted-ack-{source}-{job_id}",
        )
        if st.button("登记为已投递", type="primary"):
            if not submitted_ack:
                st.error("请先确认你已在原网站完成最终提交")
            else:
                try:
                    repository.update_status(
                        source,
                        job_id,
                        "submitted",
                        submission_evidence=evidence,
                    )
                    st.success("已保存提交证据并登记为已投递。")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    elif status == "blocked":
        col1, col2 = st.columns(2)
        if col1.button("卡点已处理，回到申请页", type="primary"):
            repository.update_status(source, job_id, "opened")
            st.rerun()
        if col2.button("不再处理，跳过"):
            repository.update_status(source, job_id, "skipped")
            st.rerun()
    elif status == "submitted":
        st.success(f"提交证据：{record['submission_evidence']}")
        outcome_cols = st.columns(2)
        if outcome_cols[0].button("标记 Offer", type="primary"):
            repository.update_status(source, job_id, "offer")
            st.rerun()
        if outcome_cols[1].button("标记未通过"):
            repository.update_status(source, job_id, "rejected")
            st.rerun()

    if status in {"confirmed", "opened"}:
        blocker_reason = st.text_input(
            "遇到卡点时记录原因",
            placeholder="例如：需要本人回答工作资格问题 / 出现验证码",
            key=f"blocker-{source}-{job_id}",
        )
        if st.button("暂停并记录卡点"):
            if not blocker_reason.strip():
                st.error("请先填写卡点原因")
            else:
                repository.update_details(
                    source,
                    job_id,
                    notes=blocker_reason,
                    next_action="需要用户处理",
                    current_stage="卡点待处理",
                )
                repository.update_status(source, job_id, "blocked")
                st.rerun()

    with st.expander("编辑备注和下一步"):
        notes = st.text_area("备注", value=str(record["notes"]), key=f"notes-{source}-{job_id}")
        next_action = st.text_input(
            "下一步", value=str(record["next_action"]), key=f"next-{source}-{job_id}"
        )
        current_stage = st.text_input(
            "当前阶段", value=str(record["current_stage"]), key=f"stage-{source}-{job_id}"
        )
        if st.button("保存记录", key=f"save-{source}-{job_id}"):
            repository.update_details(
                source,
                job_id,
                notes=notes,
                next_action=next_action,
                current_stage=current_stage,
            )
            st.success("已保存。")


def render_follow_up_tab(repository: ApplicationRepository) -> None:
    follow_ups = repository.list_follow_ups()
    today = date.today()
    next_week = today + timedelta(days=7)
    upcoming = [
        row
        for row in follow_ups
        if row["status"] == "scheduled"
        and today.isoformat() <= str(row["event_date"]) <= next_week.isoformat()
    ]
    st.metric("未来 7 天安排", len(upcoming))

    if upcoming:
        st.dataframe(
            [
                {
                    "日期": row["event_date"],
                    "时间": row["event_time"],
                    "公司": row["company"],
                    "岗位": row["title"],
                    "安排": row["event_type"],
                    "备注": row["notes"],
                }
                for row in upcoming
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("未来 7 天暂无已安排事项。")

    submitted = [row for row in repository.list_all() if row["status"] == "submitted"]
    st.markdown("#### 添加面试、笔试或跟进")
    if not submitted:
        st.caption("只有登记了提交证据的岗位才能创建日程。")
    else:
        options = {record_label(row): row for row in submitted}
        selected = options[st.selectbox("已投递岗位", list(options), key="follow-up-job")]
        date_col, time_col = st.columns(2)
        event_date = date_col.date_input("日期", value=today + timedelta(days=1))
        event_time = time_col.time_input("时间", value=time(9, 0))
        event_type = st.text_input("安排内容", placeholder="例如：一轮面试 / 完成在线测评")
        notes = st.text_input("备注（可选）", key="follow-up-notes")
        if st.button("添加日程", type="primary"):
            try:
                repository.add_follow_up(
                    str(selected["source"]),
                    str(selected["job_id"]),
                    event_date=event_date.isoformat(),
                    event_time=event_time.strftime("%H:%M"),
                    event_type=event_type,
                    notes=notes,
                )
                st.success("日程已添加，并同步更新岗位当前阶段。")
                st.rerun()
            except (ValueError, KeyError) as exc:
                st.error(str(exc))

    if follow_ups:
        st.markdown("#### 全部日程")
        follow_options = {
            f"{row['event_date']} {row['event_time']}｜{row['company']}｜{row['event_type']}｜#{row['id']}": row
            for row in follow_ups
        }
        chosen = follow_options[st.selectbox("选择日程", list(follow_options), key="follow-up-select")]
        st.write(
            f"状态：{FOLLOW_UP_LABELS.get(str(chosen['status']), chosen['status'])}｜"
            f"岗位：{chosen['title']}"
        )
        if chosen["status"] == "scheduled":
            action_cols = st.columns(2)
            if action_cols[0].button("标记已完成"):
                repository.update_follow_up_status(int(chosen["id"]), "completed")
                st.rerun()
            if action_cols[1].button("取消日程"):
                repository.update_follow_up_status(int(chosen["id"]), "cancelled")
                st.rerun()


def render_safety_tab() -> None:
    st.subheader("本版本的安全约束")
    st.markdown(
        "- 简历在本机解析，不发送给岗位数据接口。\n"
        "- 自动找岗只扫描内置、受控的 Greenhouse board；使用缓存和请求间隔，不爬搜索引擎。\n"
        "- JD 定制只重排或突出原简历事实；缺口会单独提示，不会自动补写缺失技能。\n"
        "- 定制版按岗位独立保存，不覆盖中英文基准简历。\n"
        "- 岗位详情中的一次点击只授权当前一个岗位，不会循环批量投递。\n"
        "- 一键功能只填写姓名、邮箱、电话、地点和选中的简历，永不点击最终提交。\n"
        "- 验证码、登录、身份、工作资格、薪资、法律问题和未知必填题交给用户本人。\n"
        "- 只有保存明确提交证据后，才计入“已投递”。\n"
        "- 不提供无限循环或批量提交。\n"
        "- 个人资料和中英文简历只保存在本机私有目录，不写入日志或启动参数。\n"
        "- 导出 CSV 会处理公式注入风险；个人资料、数据库和浏览器会话被忽略。"
    )
    st.subheader("从 JobHuntBot 吸收的设计")
    st.markdown(
        "借鉴了它的岗位池、申请进度、提交证据、卡点记录和 7 天日程。"
        "数据层仍使用事务化 SQLite 和稳定的来源 + 岗位 ID，未复制其存在安全隐患的 HTML/CSV 写入实现。"
    )
    st.caption("外部项目的原始副本保存在 vendor/JobHuntBot，仅作为只读参考。")


st.set_page_config(page_title="AI Job Agent", page_icon="✦", layout="wide")
require_access(SETTINGS.access_password, required=SETTINGS.cloud_deployment)

# Imported after set_page_config so the home module cannot accidentally emit a
# Streamlit command before the page configuration is established.
from ai_job_agent.ui import render_agent_home

repository = ApplicationRepository(DATABASE)

with st.sidebar:
    st.subheader("AI Job Agent")
    st.caption("一次上传，安全准备多份官网申请")
    if SETTINGS.cloud_deployment:
        st.info("云端安全版：仅解析、匹配和投递清单；不控制本机浏览器。")
    st.divider()
    st.subheader("安全边界")
    st.markdown(
        "- 不绕过验证码或网站限制\n"
        "- 不自动连续或批量提交\n"
        "- 不猜测敏感答案\n"
        "- 外企默认英文简历\n"
        "- 真实网站逐岗位处理"
    )
    st.divider()
    st.caption(f"AI Job Agent：{SETTINGS.database_path.name}")
    st.caption(f"高级工具：{DATABASE.name}")
    if SETTINGS.access_password and st.button("锁定当前页面", width="stretch"):
        lock_current_session()
        st.rerun()

agent_tab, profile_tab, match_tab, tracking_tab, follow_up_tab, safety_tab = st.tabs(
    ["AI 求职 Agent", "申请资料", "真实岗位工具", "投递记录", "面试与跟进", "安全与隐私"]
)
with agent_tab:
    render_agent_home()
with profile_tab:
    render_profile_tab()
with match_tab:
    render_match_tab(repository)
with tracking_tab:
    render_tracking_tab(repository)
with follow_up_tab:
    render_follow_up_tab(repository)
with safety_tab:
    render_safety_tab()
