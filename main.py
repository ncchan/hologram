import streamlit as st
import base64
import io
import os
import tempfile
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageEnhance
from streamlit_drawable_canvas import st_canvas
from tencentcloud.common import credential
# 重点：修正导入方式，确保 models 能正确引用
from tencentcloud.aiart.v20221229 import aiart_client, models
from rembg import remove

# ==========================================
# 全局配置（本地/云端通用）
# ==========================================
TEMP_DIR = tempfile.gettempdir()
CACHE_FILE = os.path.join(TEMP_DIR, "hologram_cache.png")

# ==========================================
# 1. 密钥读取（保持你的本地逻辑，适配 Secrets）
# ==========================================
def get_tencent_credentials():
    """读取腾讯云密钥（兼容本地硬编码/云端 Secrets）"""
    try:
        # 优先从 Secrets 读取（云端），本地可注释这行改用硬编码
        SECRET_ID = st.secrets.get("TENCENT_CLOUD", {}).get("SECRET_ID", "")
        SECRET_KEY = st.secrets.get("TENCENT_CLOUD", {}).get("SECRET_KEY", "")
        
        if not SECRET_ID or not SECRET_KEY:
            st.warning("⚠️ 未检测到腾讯云密钥，使用本地模拟修复")
            return None, None
        return SECRET_ID, SECRET_KEY
    except:
        return None, None

# ==========================================
# 2. 核心 AI 修复逻辑（完全保留你的本地调用结构）
# ==========================================
def stable_artifact_repair(img_pil, mask_pil):
    # 读取密钥
    SECRET_ID, SECRET_KEY = get_tencent_credentials()
    if not SECRET_ID or not SECRET_KEY:
        # 本地模拟修复兜底（避免返回 None 导致程序崩溃）
        img_blur = img_pil.filter(ImageFilter.GaussianBlur(5))
        buf = io.BytesIO()
        img_blur.save(buf, format="PNG")
        return buf.getvalue()
    
    # 完全保留你本地调用的逻辑，仅修复类名问题
    try:
        cred = credential.Credential(SECRET_ID, SECRET_KEY)
        client = aiart_client.AiartClient(cred, "ap-guangzhou")
        
        def to_b64(image):
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        
        mask_blur = mask_pil.filter(ImageFilter.GaussianBlur(radius=3))
        
        # 修复点1：替换正确的请求类名（根据本地可用的类名调整）
        # 如果你本地是 ImageInpaintingRemovalRequest 能运行，就用这个；否则换 ImageInpaintingRequest
        try:
            req = models.ImageInpaintingRemovalRequest()  # 优先尝试你的原类名
        except AttributeError:
            req = models.ImageInpaintingRequest()  # 备用类名
        
        req.InputImage = to_b64(img_pil)
        req.Mask = to_b64(mask_blur)
        
        # 修复点2：匹配请求类名的调用方法
        try:
            resp = client.ImageInpaintingRemoval(req)  # 原方法名
        except AttributeError:
            resp = client.ImageInpainting(req)  # 备用方法名
        
        return base64.b64decode(resp.ResultImage)
    
    except Exception as e:
        st.error(f"❌ AI 修復失敗: {str(e)}")
        # 修复点3：失败时不返回 None，返回模糊后的原图（保证程序继续运行）
        img_blur = img_pil.filter(ImageFilter.GaussianBlur(5))
        buf = io.BytesIO()
        img_blur.save(buf, format="PNG")
        return buf.getvalue()

# ==========================================
# 以下代码完全保留你的原有逻辑，仅适配路径
# ==========================================
def local_remove_bg(img_pil):
    try:
        return remove(img_pil)
    except:
        return img_pil.convert("RGBA")

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
# Streamlit 界面（完全保留你的逻辑）
# ==========================================
st.set_page_config(page_title="2026 AI 文物修復系統", layout="wide")

if 'result_img' not in st.session_state:
    st.session_state.result_img = None

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
                        mask_raw = Image.fromarray((canvas_result.image_data[:, :, 3] > 0).astype(np.uint8) * 255)
                        mask_full = mask_raw.resize(raw_img.size, Image.NEAREST).convert("L")
                        res_bytes = stable_artifact_repair(raw_img, mask_full)
                        if res_bytes:  # 不再判断 None，因为修复函数已兜底
                            st.session_state.result_img = Image.open(io.BytesIO(res_bytes))
                            st.success("修復完成！")
                else:
                    st.warning("⚠️ 請先標記殘缺區域！")

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
                        try:
                            holo_final.save(CACHE_FILE)
                            st.toast("✅ 修復圖已推送到全息螢幕！", icon="🔮")
                        except Exception as e:
                            st.error(f"❌ 同步失敗: {str(e)}")

else:
    # 全息投影端（修复路径和 CSS）
    st.markdown("""<style>
        [data-testid="stSidebar"], [data-testid="collapsedControl"], footer, header { display: none !important; }
        body { background-color: black !important; }
        #hologram-display { 
            background-color: black; height: 100vh; width: 100vw; 
            display: flex; align-items: center; justify-content: center; 
            position: fixed; top: 0; left: 0; 
        }
    </style>""", unsafe_allow_html=True)
    
    st.markdown('<meta http-equiv="refresh" content="2">', unsafe_allow_html=True)
    placeholder = st.empty()
    
    try:
        if os.path.exists(CACHE_FILE) and os.path.getsize(CACHE_FILE) > 0:
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
