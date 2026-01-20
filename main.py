import streamlit as st
import base64
import io
import os
import numpy as np
import cv2
from PIL import Image, ImageFilter, ImageOps, ImageEnhance
from streamlit_drawable_canvas import st_canvas
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.aiart.v20221229 import aiart_client
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from rembg import remove, new_session

# ==========================================
# 🔴 全域配置
# ==========================================
CACHE_FILE = "hologram_cache.png"

# 獲取當前 URL 參數 (兼容舊版本寫法)
query_params = st.query_params
current_view = query_params.get("view", ["repair"])[0]

# 設定頁面配置
st.set_page_config(page_title="2026 AI 文物修復系統", layout="wide")

# ==========================================
# 1. 金鑰獲取函數
# ==========================================
def get_credentials():
    try:
        SECRET_ID = st.secrets.get("TENCENT_CLOUD", {}).get("SECRET_ID", "")
        SECRET_KEY = st.secrets.get("TENCENT_CLOUD", {}).get("SECRET_KEY", "")
        if not SECRET_ID or not SECRET_KEY:
            st.warning("⚠️ 未檢測到金鑰，將使用本地模擬修復。")
            return None, None
        return SECRET_ID, SECRET_KEY
    except:
        return None, None

# ==========================================
# 2. 核心 AI 邏輯
# ==========================================
def stable_artifact_repair(img_pil, mask_pil):
    try:
        SECRET_ID, SECRET_KEY = get_credentials()
        
        # 本地模拟模式（無金鑰時）
        if not SECRET_ID or not SECRET_KEY:
            st.info("ℹ️ 演示模式：生成智能模糊修復效果")
            img_array = np.array(img_pil)
            mask_array = np.array(mask_pil) / 255.0
            blurred = cv2.GaussianBlur(img_array, (15,15), 0)
            result_array = img_array * (1 - mask_array[:, :, np.newaxis]) + blurred * mask_array[:, :, np.newaxis]
            result_img = Image.fromarray(result_array.astype(np.uint8))
            buf = io.BytesIO()
            result_img.save(buf, format="PNG")
            return buf.getvalue()

        # 腾讯云接口調用
        cred = credential.Credential(SECRET_ID, SECRET_KEY)
        httpProfile = HttpProfile()
        httpProfile.endpoint = "aiart.tencentcloudapi.com"
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile
        client = aiart_client.AiartClient(cred, "ap-guangzhou", clientProfile)
        
        def to_b64(image):
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        
        mask_blur = mask_pil.filter(ImageFilter.GaussianBlur(radius=3))
        params = {
            "Image": to_b64(img_pil),
            "Mask": to_b64(mask_blur),
            "Action": "ImageInpainting"
        }
        
        resp = client.call("ImageInpainting", params)
        if resp and "ResultImage" in resp:
            return base64.b64decode(resp["ResultImage"])
        else:
            st.warning("⚠️ 接口返回無結果，使用本地模拟")
            img_blur = img_pil.filter(ImageFilter.GaussianBlur(3))
            buf = io.BytesIO()
            img_blur.save(buf, format="PNG")
            return buf.getvalue()
            
    except TencentCloudSDKException as e:
        st.error(f"❌ 腾讯云API錯誤: {str(e)}")
        img_blur = img_pil.filter(ImageFilter.GaussianBlur(3))
        buf = io.BytesIO()
        img_blur.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        st.error(f"❌ AI 修復失敗: {str(e)}")
        buf = io.BytesIO()
        img_pil.save(buf, format="PNG")
        return buf.getvalue()

def local_remove_bg(img_pil):
    try:
        session = new_session("isnet-general-use")
        return remove(img_pil, session=session)
    except Exception as e:
        st.warning(f"⚠️ AI去背失敗，使用顏色去背: {str(e)}")
        img_rgba = img_pil.convert("RGBA")
        datas = img_rgba.getdata()
        new_data = []
        for item in datas:
            if item[0] > 240 and item[1] > 240 and item[2] > 240:
                new_data.append((255, 255, 255, 0))
            else:
                new_data.append(item)
        img_rgba.putdata(new_data)
        return img_rgba

# ==========================================
# 3. 全息投影演算法
# ==========================================
def create_pseudo_3d_hologram(img_pil, is_transparent=True):
    try:
        bg_size = 1024
        hologram_bg = Image.new("RGBA", (bg_size, bg_size), (0, 0, 0, 255))
        img_ready = ImageEnhance.Contrast(img_pil).enhance(1.4)
        img_ready.thumbnail((380, 380))
        
        front = img_ready
        back = ImageOps.mirror(img_ready).rotate(180)
        side_w = int(img_ready.width * 0.8)
        left = img_ready.resize((side_w, img_ready.height)).rotate(270, expand=True)
        right = ImageOps.mirror(img_ready).resize((side_w, img_ready.height)).rotate(90, expand=True)
        
        cx, sy = (bg_size - img_ready.width) // 2, (bg_size - left.height) // 2
        
        m_f = front if is_transparent else None
        m_b = back if is_transparent else None
        m_l = left if is_transparent else None
        m_r = right if is_transparent else None

        hologram_bg.paste(front, (cx, 70), m_f)
        hologram_bg.paste(back, (cx, bg_size - img_ready.height - 70), m_b)
        hologram_bg.paste(left, (70, sy), m_l)
        hologram_bg.paste(right, (bg_size - right.width - 70, sy), m_r)
        return hologram_bg.convert("RGB")
    except Exception as e:
        st.error(f"❌ 生成全息圖失敗: {str(e)}")
        return Image.new("RGB", (1024, 1024), (0, 0, 0))

# ==========================================
# 4. 頁面渲染與導航
# ==========================================

# --- 側邊欄導航 (修復版：兼容所有 Streamlit 版本) ---
with st.sidebar:
    st.header("⚙️ 系統選單")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # 按鈕邏輯：直接修改 query_params，這會自動觸發頁面刷新
        if st.button("🎨 專家修復端", use_container_width=True):
            st.query_params.clear()
            st.query_params["view"] = "repair"

    with col2:
        if st.button("🌌 全息投影端", use_container_width=True):
            st.query_params.clear()
            st.query_params["view"] = "holo"

# --- 邏輯分流 ---

if current_view == "holo":
    # ==========================================
    # 🌌 全息投影端
    # ==========================================
    # 隱藏側邊欄 (使用 CSS 強制隱藏，避免干擾觀看)
    st.markdown("""
        <style>
            [data-testid="stSidebar"] { display: none !important; }
            [data-testid="collapsedControl"] { display: none !important; }
            footer { visibility: hidden; }
            body { background-color: black !important; }
        </style>
    """, unsafe_allow_html=True)

    # 🔴 核心：Meta Refresh 自動刷新 (每 2 秒)
    # 這是最穩定的自動刷新方式，且 URL 帶有 view=holo，所以不會跳回
    st.markdown('<meta http-equiv="refresh" content="2">', unsafe_allow_html=True)

    # 創建顯示區域
    placeholder = st.empty()

    try:
        # 檢查圖片是否存在且有效
        if os.path.exists(CACHE_FILE) and os.path.getsize(CACHE_FILE) > 0:
            img = Image.open(CACHE_FILE)
            
            # 轉換為 Base64
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode()
            
            with placeholder.container():
                st.markdown(f"""
                    <div style="
                        display: flex; 
                        justify-content: center; 
                        align-items: center; 
                        height: 100vh; 
                        background: black;
                    ">
                        <img src="data:image/png;base64,{img_b64}" style="max-height: 90vh; border: 2px solid #00ff00;">
                    </div>
                """, unsafe_allow_html=True)
        else:
            # 顯示等待畫面
            with placeholder.container():
                st.markdown(f"""
                    <div style="
                        display: flex; 
                        justify-content: center; 
                        align-items: center; 
                        height: 100vh; 
                        background: black; 
                        color: #00ff00; 
                        font-size: 24px;
                    ">
                        📡 等待修復端同步圖像...
                    </div>
                """, unsafe_allow_html=True)
                
    except Exception as e:
        with placeholder.container():
            st.markdown(f"""
                <div style="
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    height: 100vh; 
                    background: black; 
                    color: red;
                ">
                    載入錯誤: {str(e)}
                </div>
            """, unsafe_allow_html=True)

else:
    # ==========================================
    # 🎨 專家修復端
    # ==========================================
    st.title("🏛️ 文物修復主控台")
    
    st.sidebar.divider()
    stroke_w = st.sidebar.slider("筆觸大小", 5, 100, 25)
    
    # 橡皮擦功能
    tool_mode = st.sidebar.radio("工具", ("✏️ 畫筆模式", "🧽 橡皮擦模式"))
    stroke_color = "#FF0000" if tool_mode == "✏️ 畫筆模式" else "#00000000"
    drawing_mode = "freedraw"
    
    h_type = st.sidebar.radio("全息類型", ("立體文物 (自動去背)", "畫作 (保留背景)"))
    file = st.sidebar.file_uploader("上傳文物圖片", type=["jpg", "png", "jpeg"])

    # 初始化 Session State
    if 'result_img' not in st.session_state:
        st.session_state.result_img = None
    if 'mask_img' not in st.session_state:
        st.session_state.mask_img = None

    if file:
        raw_img = Image.open(file).convert("RGB")
        display_w = 600
        display_h = int(raw_img.height * (display_w / raw_img.width))
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🖍️ 標記殘缺區域")
            canvas_result = st_canvas(
                fill_color="rgba(255, 255, 255, 0.0)",
                stroke_width=stroke_w,
                stroke_color=stroke_color,
                background_image=raw_img.resize((display_w, display_h)),
                update_streamlit=True,
                height=display_h,
                width=display_w,
                drawing_mode=drawing_mode,
                key="main_editor_canvas"
            )

            # 保存遮罩
            if canvas_result.image_data is not None:
                mask_raw = Image.fromarray((canvas_result.image_data[:, :, 0] > 0).astype(np.uint8) * 255)
                mask_full = mask_raw.resize(raw_img.size, Image.NEAREST).convert("L")
                st.session_state.mask_img = mask_full

        with col2:
            st.subheader("✨ 修復與同步")
            if st.button("🚀 開始 AI 修復"):
                if st.session_state.mask_img is not None:
                    with st.spinner("AI 正在分析並補全..."):
                        res_bytes = stable_artifact_repair(raw_img, st.session_state.mask_img)
                        if res_bytes:
                            st.session_state.result_img = Image.open(io.BytesIO(res_bytes))
                            st.success("✅ 修復完成！")
                else:
                    st.warning("⚠️ 請先標記殘缺區域！")

            # 顯示修復結果並提供同步
            if st.session_state.result_img:
                st.image(st.session_state.result_img, caption="AI 修復結果", width=400)
                
                if st.button("🔮 同步修復圖到全息螢幕", type="primary"):
                    with st.spinner("同步中..."):
                        img_to_sync = st.session_state.result_img
                        is_transparent = "去背" in h_type
                        
                        if is_transparent:
                            processed_img = local_remove_bg(img_to_sync)
                        else:
                            processed_img = img_to_sync.convert("RGBA")
                        
                        holo_final = create_pseudo_3d_hologram(processed_img, is_transparent)
                        holo_final.save(CACHE_FILE)
                        st.toast("✅ 修復圖已推送到全息螢幕！", icon="🔮")
