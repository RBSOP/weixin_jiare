"""create-order 页面数据采集 - 拦截 getLivePromotionOrderDetail，解析并保存配置"""
import json
import re

from playwright.sync_api import Page, Response

from db import save_order_create_config, update_order_bid_roi_if_empty
from logger import TAG_CREATE_ORDER, log
from screenshot_util import capture_page_screenshot

API_PATTERN = "getLivePromotionOrderDetail"


def _parse_json_body(body: bytes) -> dict | None:
    text = body.decode("utf-8", errors="replace")
    return json.loads(text)


def _map_promotion_type(v) -> str:
    """加热对象类型：1=短视频 2=直播间"""
    m = {1: "短视频", 2: "直播间"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_order_type(v) -> str:
    """加热订单类型：1=标准订单 2=全域订单 3=长期计划"""
    m = {1: "标准订单", 2: "全域订单", 3: "长期计划"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_heating_method(v) -> str:
    """加热方式：控成本加热/放量加热等，11=控成本 10=放量(观看量)"""
    m = {11: "控成本加热", 10: "观看量", 12: "互动量", 8: "直播间成交"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_priority_target(v) -> str:
    """优先提升目标：成交ROI/观看量/互动量"""
    m = {11: "成交ROI", 10: "观看量", 12: "互动量", 8: "直播间成交"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_delivery_speed(v) -> str:
    """放量模式：1=匀速放量 2=快速放量"""
    m = {1: "匀速放量", 2: "快速放量"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_material_flag(v) -> str:
    """加热素材：1=直接加热直播间 2=短视频加热直播间"""
    m = {1: "直接加热直播间", 2: "短视频加热直播间"}
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_gender(v) -> str:
    """观众性别：0=不限 1=男 2=女（suggest.gender 可能为数组如 [2]）"""
    m = {0: "不限", 1: "男", 2: "女"}
    if isinstance(v, list) and v:
        v = v[0]
    return m.get(int(v) if v is not None else 0, str(v) if v else "")


def _map_age_range(codes) -> list[str]:
    """观众年龄：1=18-23 2=24-30 3=31-40 4=41-50 5=51岁以上（suggest.ageRange 为数组如 [4,5]）"""
    m = {1: "18-23岁", 2: "24-30岁", 3: "31-40岁", 4: "41-50岁", 5: "51岁以上"}
    if not codes:
        return []
    return [m.get(int(c), str(c)) for c in (codes if isinstance(codes, list) else [codes]) if c is not None]


def _extract_audience_interests_from_popup(page: Page) -> str:
    """
    点击观众兴趣框，等待弹窗「选择兴趣领域」出现，采集「已添加兴趣」列表中的全部标签，关闭弹窗后返回。
    返回格式：内衣、女装、美妆工具、...（用顿号连接）
    """
    out = ""
    try:
        # 定位观众兴趣输入框（显示「已添加XX等Y个标签」的可点击区域）
        interest_box = page.get_by_text(re.compile(r"已添加.+等\d+个标签")).first
        if interest_box.count() == 0:
            return ""
        interest_box.click()
        page.wait_for_timeout(500)
        # 等待弹窗标题出现
        dialog = page.locator("text=选择兴趣领域").first
        if dialog.count() == 0:
            log(TAG_CREATE_ORDER, "观众兴趣弹窗未出现，跳过弹窗采集")
            return ""
        page.wait_for_timeout(300)
        # 精确采集：弹窗内 .tag-list-container 下的 span.content 即为兴趣标签
        container = page.locator(".tag-list-container")
        interests = []
        if container.count() > 0:
            interests = [t.strip() for t in container.locator("span.content").all_text_contents() if t and t.strip()]
        if interests:
            out = "、".join(interests)
            log(TAG_CREATE_ORDER, f"弹窗采集到 {len(interests)} 个兴趣: {out}")
        # 点击「完成」关闭弹窗
        done_btn = page.get_by_role("button", name="完成").first
        if done_btn.count() > 0:
            done_btn.click()
        else:
            close_btn = page.locator("text=完成").first
            if close_btn.count() > 0:
                close_btn.click()
        page.wait_for_timeout(200)
    except Exception as e:
        log(TAG_CREATE_ORDER, f"观众兴趣弹窗采集失败: {e}")
    return out


def _extract_audience_cities_from_popup(page: Page) -> str:
    """
    点击观众城市框，等待弹窗出现，采集已选城市分组标签（如 全部一线城市、全部新一线城市），关闭弹窗后返回。
    返回格式：全部一线城市、全部新一线城市、全部二线城市（用顿号连接）
    """
    out = ""
    try:
        city_box = page.get_by_text(re.compile(r"已添加.+等\d+个地区")).first
        if city_box.count() == 0:
            return ""
        city_box.click()
        page.wait_for_timeout(500)

        tags = []
        search_input = page.locator("input[placeholder='搜索城市']")
        if search_input.count() > 0:
            dt_container = search_input.locator("xpath=ancestor::dt")
            if dt_container.count() > 0:
                tags = dt_container.locator(".finder-ui-desktop-form__dropdown__value-ele__word").all_text_contents()
                tags = [t.strip() for t in tags if t and t.strip()]

        if tags:
            out = "、".join(tags)
            log(TAG_CREATE_ORDER, f"弹窗采集到 {len(tags)} 个城市分组: {out}")

        done_btn = page.get_by_role("button", name="完成").first
        if done_btn.count() > 0:
            done_btn.click()
        else:
            close_btn = page.locator("text=完成").first
            if close_btn.count() > 0:
                close_btn.click()
        page.wait_for_timeout(200)
    except Exception as e:
        log(TAG_CREATE_ORDER, f"观众城市弹窗采集失败: {e}")
    return out


def _extract_fan_authors_from_popup(page: Page) -> str:
    """
    点击粉丝层推荐的「已添加XX等N个作者」框，等待弹窗出现，采集已选作者昵称，关闭弹窗后返回。
    返回格式：FILA、FILAFUSION、FILA总部店（用顿号连接）
    """
    out = ""
    try:
        fan_box = page.get_by_text(re.compile(r"已添加.+等\d+个作者")).first
        if fan_box.count() == 0:
            return ""
        fan_box.click()
        page.wait_for_timeout(500)

        dialog = page.locator("text=根据粉丝层推荐").first
        if dialog.count() == 0:
            log(TAG_CREATE_ORDER, "粉丝层推荐弹窗未出现，跳过弹窗采集")
            return ""
        page.wait_for_timeout(300)

        authors = []
        search_input = page.locator("input[placeholder='搜索视频号昵称']")
        if search_input.count() > 0:
            dt_container = search_input.locator("xpath=ancestor::dt")
            if dt_container.count() > 0:
                elements = dt_container.locator(".finder-ui-desktop-form__dropdown__value-ele")
                for i in range(elements.count()):
                    title = elements.nth(i).get_attribute("title")
                    if title and title.strip():
                        authors.append(title.strip())
        if authors:
            out = "、".join(authors)
            log(TAG_CREATE_ORDER, f"弹窗采集到 {len(authors)} 个相似作者: {out[:80]}")

        done_btn = page.get_by_role("button", name="完成").first
        if done_btn.count() > 0:
            done_btn.click()
        else:
            close_btn = page.locator("text=完成").first
            if close_btn.count() > 0:
                close_btn.click()
        page.wait_for_timeout(200)
    except Exception as e:
        log(TAG_CREATE_ORDER, f"粉丝层推荐弹窗采集失败: {e}")
    return out


def _extract_from_page(page: Page) -> dict:
    """从页面 DOM 直接读取 预计带来商品成交金额、成交ROI、观众兴趣、观众城市、根据粉丝层推荐（不依赖 API 计算）"""
    out = {"预计带来商品成交金额": "", "成交ROI": "", "观众兴趣": "", "观众城市": "", "根据粉丝层推荐": ""}
    try:
        # 预计带来商品成交金额：从页面 DOM 直接读取
        est_loc = page.locator("text=预计带来商品成交金额").first
        if est_loc.count() > 0:
            try:
                parent = est_loc.locator("xpath=..").first
                pt = parent.text_content() or ""
                m = re.search(r"(\d+(?:\.\d+)?)\s*元", pt)
                if m:
                    out["预计带来商品成交金额"] = f"{m.group(1)}元"
            except Exception:
                pass
        if not out["预计带来商品成交金额"]:
            text = page.content()
            m1 = re.search(r"预计带来商品成交金额[\s\S]{0,300}?(\d+(?:\.\d+)?)\s*元", text)
            if m1:
                out["预计带来商品成交金额"] = f"{m1.group(1)}元"
        # 成交ROI（每个订单成交的出价）：从 #live-custom-bid-input 输入框读取
        bid_input = page.locator("#live-custom-bid-input input.finder-ui-desktop-form__input")
        if bid_input.count() > 0:
            bid_val = (bid_input.input_value() or "").strip()
            if bid_val:
                out["成交ROI"] = bid_val
                log(TAG_CREATE_ORDER, f"页面读取 出价: {bid_val}")
        # 根据粉丝层推荐：点击框→弹窗采集已选相似作者昵称
        out["根据粉丝层推荐"] = _extract_fan_authors_from_popup(page)
        # 观众兴趣：点击框→弹窗采集「已添加兴趣」列表中的全部标签
        out["观众兴趣"] = _extract_audience_interests_from_popup(page)
        if not out["观众兴趣"]:
            # 回退：从页面 DOM 正则匹配
            text = page.content()
            for m in re.finditer(r"已添加([^<]{2,80})等(\d+)个标签", text):
                raw = m.group(1).strip()
                if "interestTag" not in raw and "{" not in raw and "tagLevel" not in raw and len(re.findall(r"[\u4e00-\u9fff]", raw)) >= 1:
                    out["观众兴趣"] = f"已添加{raw}等{m.group(2)}个标签"
                    break
        # 观众城市：点击框→弹窗采集已选城市分组标签
        out["观众城市"] = _extract_audience_cities_from_popup(page)
    except Exception as e:
        log(TAG_CREATE_ORDER, f"页面提取失败: {e}")
    return out


def _parse_detail_to_config(data: dict, page_values: dict | None = None) -> dict | None:
    """
    将 getLivePromotionOrderDetail 响应解析为带大标题的结构化配置
    """
    raw = data.get("data") or {}
    info = raw.get("livePromotionOrderDetailInfo") or raw.get("orderInfo") or raw
    if not info:
        return None

    order_info = info.get("orderInfo") or info
    acct = order_info.get("acctInfo") or {}
    suggest = order_info.get("suggest") or {}
    payment_info = order_info.get("paymentInfo") or {}

    cost_quota = info.get("costQuota") or order_info.get("costQuota") or 0
    cost_yuan = int(cost_quota) / 10.0 if cost_quota else 0
    target_info = order_info.get("promotionTargetInfo") or info.get("promotionTargetInfo") or {}
    roi = (
        order_info.get("bidRoi") or order_info.get("targetRoi")
        or target_info.get("bidRoi") or target_info.get("targetRoi")
        or suggest.get("bidRoi")
    )
    if roi is None and suggest.get("roiBidX100") is not None:
        roi = suggest["roiBidX100"] / 100.0
    if not roi:
        page_bid = (page_values or {}).get("成交ROI", "") if page_values else ""
        if page_bid:
            roi = page_bid
    # 预计带来商品成交金额：优先用页面读取值，不再计算
    estimated_str = (page_values or {}).get("预计带来商品成交金额", "") if page_values else ""
    if not estimated_str:
        estimated_yuan = (
            info.get("estimatedDealAmount") or order_info.get("estimatedDealAmount")
            or info.get("estimateAmount") or order_info.get("estimateAmount")
            or info.get("expectedTransactionAmount") or order_info.get("expectedTransactionAmount")
        )
        if estimated_yuan is not None:
            est_val = float(estimated_yuan)
            if est_val >= 1000:
                est_val = est_val / 10.0
            estimated_str = f"{int(est_val)}元" if est_val >= 1 else f"{est_val}元"
    # 加热时长：duration 可能为秒(3600=1小时)或小时(1=1小时)
    duration_raw = info.get("duration") or order_info.get("duration") or order_info.get("promotionDuration") or 0
    duration_val = int(duration_raw) if duration_raw else 0
    if duration_val >= 3600:
        duration_str = f"{duration_val // 3600}小时"
    elif duration_val >= 60:
        duration_str = f"{duration_val // 60}分钟"
    elif 0 < duration_val < 60:
        duration_str = f"{duration_val}小时"
    else:
        duration_str = ""

    gender = suggest.get("gender")
    age_range = suggest.get("ageRange") or suggest.get("ageRangeList") or []
    city_ids = suggest.get("cityIds") or []
    interest_tag = suggest.get("interestTagV3") or suggest.get("interestTag") or []
    tag_id_to_name = {}
    for m in (raw.get("interestTagList") or raw.get("tagList") or suggest.get("interestTagList") or []):
        if isinstance(m, dict):
            tid = m.get("id") or m.get("interestTag") or m.get("tagId")
            name = m.get("name") or m.get("tagName") or m.get("tagNameCn") or m.get("label") or ""
            if tid is not None and name:
                tag_id_to_name[str(tid)] = name

    # 观众兴趣：优先用页面读取值（如 已添加内衣、女装 等10个标签）
    interest_str = (page_values or {}).get("观众兴趣", "") if page_values else ""
    if not interest_str:
        interest_labels = []
        if isinstance(interest_tag, list):
            for t in interest_tag:
                if isinstance(t, dict):
                    tag_info = t.get("tagInfo") or {}
                    name = (
                        t.get("tagName") or t.get("tagNameCn") or t.get("name")
                        or t.get("label") or t.get("title") or t.get("categoryName")
                        or tag_info.get("name") or tag_info.get("tagName") or tag_info.get("label")
                    )
                    if not name and t.get("interestTag") is not None:
                        name = tag_id_to_name.get(str(t.get("interestTag")), "")
                    if name and isinstance(name, str) and not name.startswith("{"):
                        interest_labels.append(name)
                elif isinstance(t, str) and t and not t.startswith("{"):
                    interest_labels.append(t)
        if interest_labels:
            head = "、".join(interest_labels[:5])
            suffix = f"等{len(interest_labels)}个标签" if len(interest_labels) > 5 else ""
            interest_str = f"已添加{head}{suffix}"
        else:
            tag_count = len(interest_tag) if isinstance(interest_tag, list) else 0
            interest_str = f"已添加{tag_count}个标签" if tag_count else "未添加"

    city_str = "全部地区" if not city_ids else f"已选{len(city_ids)}个城市"
    city_groups = (page_values or {}).get("观众城市", "") if page_values else ""
    if city_groups:
        city_str = f"{city_str}（{city_groups}）"

    fan_target = suggest.get("fanTarget") or suggest.get("similarAuthorFan")
    list_target = suggest.get("listTarget") or suggest.get("specifiedList")
    targeting_type = "自定义" if (gender is not None or age_range or city_ids or interest_tag) else "全部"
    fan_recommend = "选择相似作者的粉丝" if fan_target else "不限"
    fan_authors = (page_values or {}).get("根据粉丝层推荐", "") if page_values else ""
    if fan_authors:
        fan_recommend = f"{fan_recommend}（{fan_authors}）"
    list_recommend = "选择指定名单" if list_target else "不限"

    device = suggest.get("deviceTypes") or suggest.get("device") or suggest.get("deviceType")
    device_str = "不限"
    if device:
        if isinstance(device, list):
            if 1 in device and 2 not in device:
                device_str = "iOS"
            elif 2 in device and 1 not in device:
                device_str = "安卓"
        elif device == 1:
            device_str = "iOS"
        elif device == 2:
            device_str = "安卓"

    voucher_info = payment_info.get("voucherInfo") or {}
    has_voucher = bool(voucher_info.get("voucherId") or voucher_info.get("couponId"))
    growth_card = payment_info.get("growthCardInfo") or {}
    has_growth = bool(growth_card.get("cardId"))
    cost_guarantee = payment_info.get("costGuaranteeInfo") or {}
    has_guarantee = bool(cost_guarantee.get("voucherId"))
    if has_voucher:
        payment_str = "优惠券"
    elif has_growth:
        payment_str = "电商成长卡"
    elif has_guarantee:
        payment_str = "成本保障券"
    else:
        payment_str = "不使用"

    bid_roi = str(roi) if roi is not None else ""
    order_name = order_info.get("orderName") or order_info.get("promotionName") or ""

    config = {
        "选择加热类型": {
            "加热对象类型": _map_promotion_type(order_info.get("promotionType")),
            "加热订单类型": _map_order_type(order_info.get("orderType")),
        },
        "选择加热对象": {
            "主播昵称": acct.get("nickName") or "",
        },
        "选择加热方案": {
            "预计带来商品成交金额": estimated_str,
            "基础信息": {
                "订单名称": order_name,
                "加热方式": _map_heating_method(order_info.get("promotionTarget")),
                "放量模式": _map_delivery_speed(order_info.get("deliverySpeedMode")),
                "优先提升目标": _map_priority_target(order_info.get("promotionTarget")),
                "成交ROI": str(bid_roi) if bid_roi else "",
                "加热素材": _map_material_flag(order_info.get("materialFlag")),
            },
        },
        "预算与时间": {
            "订单预算": f"{cost_yuan:.0f}元" if cost_yuan else "",
            "加热时长": duration_str,
        },
        "人群定向": {
            "定向类型": targeting_type,
            "观众性别": _map_gender(gender),
            "根据粉丝层推荐": fan_recommend,
            "根据名单推荐": list_recommend,
            "观众年龄": _map_age_range(age_range),
            "观众设备": device_str,
            "观众城市": city_str,
            "观众兴趣": interest_str,
        },
        "其他": {
            "其他支付方式": payment_str,
        },
    }
    return config


def collect_create_order_config(page: Page, promotion_id: str) -> dict | None:
    """
    跳转到 create-order 页面，拦截 getLivePromotionOrderDetail，解析并保存配置
    :return: 解析后的配置 dict，失败返回 None
    """
    from create_order import goto_create_order

    collected: list[dict] = []

    def on_response(response: Response):
        if API_PATTERN not in response.url:
            return
        if response.status not in (200, 201):
            return
        try:
            body = response.body()
            if not body:
                return
            data = _parse_json_body(body)
        except Exception:
            return
        if not data:
            return
        err = data.get("errCode")
        if err is not None and err != 0:
            log(TAG_CREATE_ORDER, f"API返回错误: errCode={err} | url={response.url[-60:]}")
            return
        collected.append(data)

    page.on("response", on_response)
    log(TAG_CREATE_ORDER, f"跳转 create-order | pid={promotion_id}")
    goto_create_order(page, promotion_id)
    page.wait_for_timeout(8000)
    page.remove_listener("response", on_response)

    page_values = _extract_from_page(page)
    if page_values.get("预计带来商品成交金额"):
        log(TAG_CREATE_ORDER, f"页面读取 预计: {page_values['预计带来商品成交金额']}")
    if page_values.get("观众兴趣"):
        log(TAG_CREATE_ORDER, f"页面读取 观众兴趣: {page_values['观众兴趣'][:50]}...")
    if page_values.get("观众城市"):
        log(TAG_CREATE_ORDER, f"页面读取 观众城市: {page_values['观众城市'][:50]}...")
    if page_values.get("根据粉丝层推荐"):
        log(TAG_CREATE_ORDER, f"页面读取 根据粉丝层推荐: {page_values['根据粉丝层推荐'][:50]}...")

    if not collected:
        log(TAG_CREATE_ORDER, f"未获取到接口数据 | pid={promotion_id}")
        return None

    last = collected[-1]
    config = _parse_detail_to_config(last, page_values)
    if config:
        save_order_create_config(promotion_id, config)
        roi_val = (config.get("选择加热方案") or {}).get("基础信息") or {}
        if isinstance(roi_val, dict) and roi_val.get("成交ROI"):
            update_order_bid_roi_if_empty(promotion_id, str(roi_val["成交ROI"]))
        log(TAG_CREATE_ORDER, f"解析并保存成功 | pid={promotion_id}")

    capture_page_screenshot(page, promotion_id, "create_config", TAG_CREATE_ORDER)
    return config
