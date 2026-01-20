import streamlit as st
import base64
import io
import time
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageEnhance, ImageDraw
from tencentcloud.common import credential
from tencentcloud.aiart.v20221229 import aiart_client, models
from rembg import remove
import matplotlib.pyplot as plt
import cv2
from streamlit_drawable_canvas import st_canvas

# ==========================================
# 1. 基礎配置（適配最新版Streamlit）
# ==========================================
st.set_page_config(page_title="2026 AI 文物修復系統", layout="wide")
plt.switch_backend('Agg')  # 避免matplotlib後端衝突

# ==========================================
# 2. 核心 AI 邏輯（金鑰配置提示優化）
# ==========================================
def get_credentials():
    """安全取得騰訊雲金鑰"""
    try:
        # 優先讀取Secrets，本地測試時可臨時替換為你的金鑰（演示後註釋）
        SECRET_ID = st.secrets.get("TENCENT_CLOUD", {}).get("SECRET_ID", "")
        SECRET_KEY = st.secrets.get("TENCENT_CLOUD", {}).get("SECRET_KEY", "")
        
        if not SECRET_ID or not SECRET_KEY:
            st.warning("⚠️ 未檢測到騰訊雲金鑰！本地測試可臨時填入金鑰，部署時請在Streamlit Secrets配置。")
            # 【本地測試用】取消下面兩行註釋，填入你的金鑰（演示後務必註釋）
            # SECRET_ID = "你的測試ID"
            # SECRET_KEY = "你的測試KEY"
            return None, None
        return SECRET_ID, SECRET_KEY
    except Exception as e:
        st.error(f"❌ 讀取金鑰失敗: {str(e)}")
        return None, None

def stable_artifact_repair(img_pil, mask_pil):
    try:
        SECRET_ID, SECRET_KEY = get_credentials()
        if not SECRET_ID or not SECRET_KEY:
            st.info("ℹ️ 使用本地模擬修復效果（無金鑰時的備用方案）")
            # 無金鑰時的備用方案：返回模糊後的原圖（演示時不影響展示流程）
            return img_pil.filter(ImageFilter.GaussianBlur(2)).tobytes()
        
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
        # 備用方案：返回原圖，避免演示中斷
        buf = io.BytesIO()
        img_pil.save(buf, format="PNG")
        return buf.getvalue()

def local_remove_bg(img_pil):
    try:
        return remove(img_pil)
    except Exception as e:
        st.warning(f"⚠️ 去背失敗，使用原始圖像: {str(e)}")
        return img_pil.convert("RGBA")

# ==========================================
# 3. 全像投影演算法
# ==========================================
def create_pseudo_3d_hologram(img_pil, is_transparent=True):
    try:
        bg_size = 1024
        hologram_bg = Image.new("RGBA", (bg_size, bg_size), (0, 0, 0, 255))
        
        enhancer = ImageEnhance.Contrast(img_pil)
        img_ready = enhancer.enhance(1.4)
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
        st.error(f"❌ 生成全像圖失敗: {str(e)}")
        return Image.new("RGB", (1024, 1024), (0, 0, 0))

# ==========================================
# 4. 筆刷標記工具（可交互繪圖版本）
# ==========================================
def init_session_state():
    default_states = {
        'result_img': None,
        'holo_img': None,
        'last_update': 0,
        'uploaded_img': None,
        'mask_img': None,
        'draw_image': None,
        'stroke_width': 25
    }
    for key, value in default_states.items():
        if key not in st.session_state:
            st.session_state[key] = value

# 創建可交互繪圖的介面（基於streamlit-drawable-canvas）
def draw_on_image(img_pil, stroke_w):
    st.subheader("🖍️ 標記殘缺區域（滑鼠拖動畫筆）")
    
    # 調整圖片尺寸，避免畫布過大影響效能
    max_size = 800
    width, height = img_pil.size
    if width > max_size or height > max_size:
        ratio = min(max_size/width, max_size/height)
        new_size = (int(width*ratio), int(height*ratio))
        img_pil = img_pil.resize(new_size, Image.Resampling.LANCZOS)
    
    # 將圖片轉為Base64，直接傳入canvas（避開image_to_url）
    buf = io.BytesIO()
    img_pil.save(buf, format="PNG")
    img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    img_url = f"data:image/png;base64,{img_base64}"
    
    # 創建可繪製的交互畫布（直接使用Base64 URL）
    canvas_result = st_canvas(
        fill_color="rgba(255, 255, 255, 0.0)",  # 填充透明
        stroke_width=stroke_w,
        stroke_color="#FF0000",  # 紅色筆刷（醒目易見）
        background_image=None,  # 不再傳入PIL對象
        background_image_url=img_url,  # 改用Base64 URL
        update_streamlit=True,
        height=img_pil.height,
        width=img_pil.width,
        drawing_mode="freedraw",  # 自由繪製模式
        key="repair_canvas",
    )

    # 處理繪製結果，生成修復用遮罩
    mask_img = None
    if canvas_result.image_data is not None:
        # 提取使用者繪製的區域（紅色通道）
        mask_np = canvas_result.image_data[:, :, 0]  # 取紅色通道
        mask_np = (mask_np > 0).astype(np.uint8) * 255  # 轉換為黑白遮罩
        mask_img = Image.fromarray(mask_np)
        st.session_state.mask_img = mask_img
        
        # 預覽遮罩效果
        col1, col2 = st.columns(2)
        with col1:
            st.image(img_pil, caption="原始圖片", use_column_width=True)
        with col2:
            st.image(mask_img, caption="標記的修復區域（遮罩）", use_column_width=True)
    
    return mask_img

# ==========================================
# 5. 使用者介面（繁體中文 + 可交互繪圖）
# ==========================================
init_session_state()

# 側邊欄
st.sidebar.header("⚙️ 模式切換")
app_mode = st.sidebar.selectbox("視窗模式", ["🎨 專家修復端", "🌌 全像投影端"])

if app_mode == "🎨 專家修復端":
    st.title("🏛️ 文物修復主控台")
    
    st.sidebar.divider()
    # 調整筆觸大小
    st.session_state.stroke_width = st.sidebar.slider("筆觸大小", 5, 100, st.session_state.stroke_width)
    h_type = st.sidebar.radio("全像類型", ("立體文物 (自動去背)", "畫作 (保留背景)"))
    file = st.sidebar.file_uploader("上傳文物圖片", type=["jpg", "png", "jpeg"])

    if file:
        try:
            raw_img = Image.open(file).convert("RGB")
            st.session_state.uploaded_img = raw_img
            display_img = raw_img.resize((600, int(raw_img.height * 600 / raw_img.width)))
            
            col1, col2 = st.columns(2)
            with col1:
                # 使用新的可交互繪圖函數
                mask_img = draw_on_image(display_img, st.session_state.stroke_width)

            with col2:
                st.subheader("✨ 修復與同步")
                if st.button("🚀 開始 AI 修復"):
                    with st.spinner("AI 正在分析並補全..."):
                        # 獲取遮罩（無標記時用默認遮罩）
                        if st.session_state.mask_img is None:
                            mask = Image.new("L", raw_img.size, 0)
                            # 默認標記中心區域（演示用）
                            draw = ImageDraw.Draw(mask)
                            draw.ellipse([raw_img.width//2-50, raw_img.height//2-50, 
                                          raw_img.width//2+50, raw_img.height//2+50], fill=255)
                            st.session_state.mask_img = mask
                        
                        # AI修復
                        res_bytes = stable_artifact_repair(raw_img, st.session_state.mask_img)
                        if res_bytes:
                            st.session_state.result_img = Image.open(io.BytesIO(res_bytes))
                            st.success("✅ 修復完成！")

                # 顯示修復結果
                if st.session_state.result_img:
                    st.image(st.session_state.result_img, caption="AI 修復結果", width=400)
                    
                    if st.button("🔮 同步修復圖到全像螢幕"):
                        with st.spinner("同步中..."):
                            img_to_sync = st.session_state.result_img
                            is_transparent = "去背" in h_type
                            
                            if is_transparent:
                                processed_img = local_remove_bg(img_to_sync)
                            else:
                                processed_img = img_to_sync.convert("RGBA")
                            
                            holo_final = create_pseudo_3d_hologram(processed_img, is_transparent)
                            st.session_state.holo_img = holo_final
                            st.session_state.last_update = time.time()
                            
                            st.toast("✅ 修復圖已推送到全像螢幕！", icon="🔮")
        except Exception as e:
            st.error(f"❌ 處理圖片失敗: {str(e)}")

else:
    # 🌌 全像投影端
    st.markdown("""<style>
        [data-testid="stSidebar"] {display: none;}
        footer {visibility: hidden;}
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
        .refresh-btn {
            position: fixed; 
            bottom: 20px; 
            right: 20px; 
            z-index: 999;
        }
    </style>""", unsafe_allow_html=True)
    
    placeholder = st.empty()
    
    # 圖像轉base64
    def pil_to_base64(img):
        buf = io.BytesIO()
        img.save(buf, format="PNG", quality=95)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    
    if st.session_state.holo_img:
        img_b64 = pil_to_base64(st.session_state.holo_img)
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
                    <div style="color: white; font-size: 24px; text-align: center;">
                        🎯 等待修復端同步圖像...
                    </div>
                </div>
            """, unsafe_allow_html=True)
    
    # 重新整理按鈕
    st.markdown(
        """
        <div class="refresh-btn">
            <button onclick="window.location.reload()" style="
                padding: 10px 20px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 16px;
                cursor: pointer;
            ">
                🔄 重新整理全像圖
            </button>
        </div>
        """,
        unsafe_allow_html=True
    )

