import streamlit as st
import base64
import io
import os
import tempfile
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageEnhance
from streamlit_drawable_canvas import st_canvas
from tencentcloud.common import credential
from tencentcloud.aiart.v20221229 import aiart_client, models
from rembg import remove

# ==========================================
# 修复：跨平台兼容的临时文件路径（本地/云端通用）
# ==========================================
# 使用 Python 标准库的 tempfile 获取安全的临时目录
TEMP_DIR = tempfile.gettempdir()
CACHE_FILE = os.path.join(TEMP_DIR, "hologram_cache.png")
# 禁用自动休眠（云端特有）
st.set_option('server.headless', True)

# ==========================================
# 1. 密钥读取 + 核心 AI 逻辑
# ==========================================
def get_tencent_credentials():
    """安全读取腾讯云密钥（本地/云端通用）"""
    try:
        SECRET_ID = st.secrets.get("TENCENT_CLOUD", {}).get("SECRET_ID", "")
        SECRET_KEY = st.secrets.get("TENCENT_CLOUD", {}).get("SECRET_KEY", "")
        
        if not SECRET_ID or not SECRET_KEY:
            st.warning("⚠️ 未检测到腾讯云密钥，将使用本地模拟修复模式")
            return None, None
        return SECRET_ID, SECRET_KEY
    except Exception as e:
        st.warning(f"⚠️ 读取密钥失败: {str(e)}，使用本地模拟修复模式")
        return None, None

def stable_artifact_repair(img_pil, mask_pil):
    # 先读取密钥
    SECRET_ID, SECRET_KEY = get_tencent_credentials()
    
    # 无密钥时使用本地模拟修复（兜底）
    if not SECRET_ID or not SECRET_KEY:
        st.info("ℹ️ 本地模拟模式：生成智能模糊修复效果")
        img_array = np.array(img_pil)
        mask_array = np.array(mask_pil) / 255.0
        # 改用 PIL 模糊，避免依赖 cv2
        blurred_img = img_pil.filter(ImageFilter.GaussianBlur(5))
        blurred_array = np.array(blurred_img)
        result_array = img_array * (1 - mask_array[:, :, np.newaxis]) + blurred_array * mask_array[:, :, np.newaxis]
        result_img = Image.fromarray(result_array.astype(np.uint8))
        buf = io.BytesIO()
        result_img.save(buf, format="PNG")
        return buf.getvalue()
    
    # 有密钥时调用腾讯云接口
    try:
        cred = credential.Credential(SECRET_ID, SECRET_KEY)
        client = aiart_client.AiartClient(cred, "ap-guangzhou")
        
        def to_b64(image):
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        
        mask_blur = mask_pil.filter(ImageFilter.GaussianBlur(radius=3))
        req = models.ImageInpaintingRemovalRequest()
        req.InputImage = to_b64(img_pil)
        req.Mask = to_b64(mask_blur)
        resp = client.ImageInpaintingRemoval(req)
        return base64.b64decode(resp.ResultImage)
    except Exception as e:
        st.error(f"❌ AI 修復失敗: {str(e)}")
        # 接口调用失败时兜底
        img_blur = img_pil.filter(ImageFilter.GaussianBlur(5))
        buf = io.BytesIO()
        img_blur.save(buf, format="PNG")
        return buf.getvalue()

def local_remove_bg(img_pil):
    try:
        return remove(img_pil)
    except Exception as e:
        st.warning(f"⚠️ 去背失敗，使用備用方案: {str(e)}")
        return img_pil.convert("RGBA")

# ==========================================
# 2. 全息投影演算法
# ==========================================
def create_pseudo_3d_hologram(img_pil, is_transparent=True):
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

# ==========================================
# 3. Streamlit 使用者介面
# ==========================================
st.set_page_config(page_title="2026 AI 文物修復系統", layout="wide")

# 初始化 Session State
if 'result_img' not in st.session_state:
    st.session_state.result_img = None
if 'last_mtime' not in st.session_state:
    st.session_state.last_mtime = 0

st.sidebar.header("⚙️ 模式切換")
app_mode = st.sidebar.selectbox("視窗模式", ["🎨 專家修復端", "🌌 全息投影端"])

if app_mode == "🎨 專家修復端":
    st.title("🏛️ 文物修復主控台")
    
    st.sidebar.divider()
    stroke_w = st.sidebar.slider("筆觸大小", 5, 100, 25)
    tool_mode = st.sidebar.radio("工具", ("畫筆模式", "編輯/刪除模式"))
    drawing_mode = "freedraw" if tool_mode == "畫筆模式" else "transform"
    
    h_type = st.sidebar.radio("全息類型", ("立體文物 (自動去背)", "畫作 (保留背景)"))
    file = st.sidebar.file_uploader("上傳文物圖片", type=["jpg", "png", "jpeg"])

    if file:
        raw_img = Image.open(file).convert("RGB")
        display_w = 600
        display_h = int(raw_img.height * (display_w / raw_img.width))
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🖍️ 標記殘缺區域")
            canvas_result = st_canvas(
                fill_color="rgba(255, 255, 255, 0.4)",
                stroke_width=stroke_w,
                stroke_color="rgba(255, 255, 255, 0.4)",
                background_image=raw_img.resize((display_w, display_h)),
                update_streamlit=True,
                height=display_h,
                width=display_w,
                drawing_mode=drawing_mode,
                key="main_editor_canvas"
            )

        with col2:
            st.subheader("✨ 修復與同步")
            if st.button("🚀 開始 AI 修復"):
                if canvas_result.image_data is not None:
                    with st.spinner("AI 正在分析並補全..."):
                        # 生成遮罩
                        mask_raw = Image.fromarray((canvas_result.image_data[:, :, 3] > 0).astype(np.uint8) * 255)
                        mask_full = mask_raw.resize(raw_img.size, Image.NEAREST).convert("L")
                        # 呼叫 AI 修復
                        res_bytes = stable_artifact_repair(raw_img, mask_full)
                        if res_bytes:
                            st.session_state.result_img = Image.open(io.BytesIO(res_bytes))
                            st.success("修復完成！")

            # 只要有修復後的圖，就顯示並提供同步按鈕
            if st.session_state.result_img:
                st.image(st.session_state.result_img, caption="AI 修復結果", width=400)
                
                if st.button("🔮 同步修復圖到全息螢幕"):
                    with st.spinner("同步中..."):
                        img_to_sync = st.session_state.result_img
                        is_transparent = "去背" in h_type
                        if is_transparent:
                            processed_img = local_remove_bg(img_to_sync)
                        else:
                            processed_img = img_to_sync.convert("RGBA")
                        
                        holo_final = create_pseudo_3d_hologram(processed_img, is_transparent)
                        # 增加文件写入的异常捕获（云端容错）
                        try:
                            holo_final.save(CACHE_FILE)
                            st.toast("✅ 修復圖已推送到全息螢幕！", icon="🔮")
                        except Exception as e:
                            st.error(f"❌ 同步失敗: {str(e)}")

else:
    # ==========================================
    # 🌌 全息投影端
    # ==========================================
    st.markdown("""<style>
        [data-testid="stSidebar"],
        [data-testid="collapsedControl"],
        footer,
        header { display: none !important; }
        body { background-color: black !important; }
        #hologram-display { 
            background-color: black; 
            height: 100vh; 
            width: 100vw; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            position: fixed; 
            top: 0; 
            left: 0; 
        }
    </style>""", unsafe_allow_html=True)
    
    st.markdown('<meta http-equiv="refresh" content="2">', unsafe_allow_html=True)
    
    placeholder = st.empty()
    
    # 检查缓存文件并显示（增加容错）
    try:
        if os.path.exists(CACHE_FILE) and os.path.getsize(CACHE_FILE) > 0:
            # 读取并显示图片
            img = Image.open(CACHE_FILE)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode()
            with placeholder.container():
                st.markdown(f"""
                    <div id="hologram-display">
                        <img src="data:image/png;base64,{img_b64}" style="max-width: 95%; max-height: 95%; object-fit: contain;">
                    </div>
                """, unsafe_allow_html=True)
        else:
            with placeholder.container():
                st.markdown(f"""
                    <div id="hologram-display">
                        <div style="color: white; font-size: 20px;">等待修復端同步圖像...</div>
                    </div>
                """, unsafe_allow_html=True)
    except Exception as e:
        with placeholder.container():
            st.markdown(f"""
                <div id="hologram-display">
                    <div style="color: red; font-size: 20px;">載入錯誤: {str(e)}</div>
                </div>
            """, unsafe_allow_html=True)
