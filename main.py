import streamlit as st
import base64
import io
import time
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageEnhance
import cv2
from tencentcloud.common import credential
from tencentcloud.aiart.v20221229 import aiart_client, models
from rembg import remove

# ==========================================
# 1. 配置與常數（適配Streamlit Cloud + Python 3.13）
# ==========================================
st.set_page_config(page_title="2026 AI 文物修復系統", layout="wide")
st.set_option('deprecation.showPyplotGlobalUse', False)
st.config.set_option("client.showErrorDetails", True)

# ==========================================
# 2. 核心 AI 邏輯
# ==========================================
def get_credentials():
    """安全取得騰訊雲金鑰"""
    try:
        SECRET_ID = st.secrets["TENCENT_CLOUD"]["SECRET_ID"]
        SECRET_KEY = st.secrets["TENCENT_CLOUD"]["SECRET_KEY"]
        return SECRET_ID, SECRET_KEY
    except KeyError:
        st.error("❌ 未配置騰訊雲金鑰！請在Streamlit Secrets中新增：")
        st.code("""
[TENCENT_CLOUD]
SECRET_ID = "你的ID"
SECRET_KEY = "你的KEY"
        """, language="toml")
        return None, None

def stable_artifact_repair(img_pil, mask_pil):
    try:
        SECRET_ID, SECRET_KEY = get_credentials()
        if not SECRET_ID or not SECRET_KEY:
            return None
        
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
        return None

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
# 4. 原生標記工具（替代streamlit-drawable-canvas）
# ==========================================
def init_session_state():
    default_states = {
        'result_img': None,
        'holo_img': None,
        'last_update': 0,
        'uploaded_img': None,
        'mask_data': None,  # 儲存手動繪製的遮罩
        'stroke_width': 25  # 筆觸大小
    }
    for key, value in default_states.items():
        if key not in st.session_state:
            st.session_state[key] = value

# 生成遮罩的輔助函數
def generate_mask_from_click(img_pil, click_coords, stroke_w):
    """根據點擊座標生成遮罩"""
    mask = Image.new("L", img_pil.size, 0)
    draw = ImageDraw.Draw(mask)
    for (x, y) in click_coords:
        # 將顯示座標轉換為原始圖像座標
        scale_x = img_pil.width / 600
        scale_y = img_pil.height / (img_pil.height * 600 / img_pil.width)
        orig_x = int(x * scale_x)
        orig_y = int(y * scale_y)
        # 繪製圓形筆觸
        draw.ellipse([orig_x - stroke_w//2, orig_y - stroke_w//2, 
                      orig_x + stroke_w//2, orig_y + stroke_w//2], 
                     fill=255)
    return mask

# ==========================================
# 5. 使用者介面（完全移除canvas依賴）
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
            display_w = 600
            display_h = int(raw_img.height * (display_w / raw_img.width))
            display_img = raw_img.resize((display_w, display_h))
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("🖍️ 標記殘缺區域")
                # 原生圖像顯示 + 點擊標記
                st.image(display_img, use_column_width=True)
                
                # 點擊座標收集
                click_x = st.number_input("點擊X座標（0-600）", 0, display_w, 300)
                click_y = st.number_input("點擊Y座標（0-{}）".format(display_h), 0, display_h, int(display_h/2))
                
                col1_1, col1_2 = st.columns(2)
                with col1_1:
                    if st.button("➕ 新增標記點"):
                        if 'click_coords' not in st.session_state:
                            st.session_state.click_coords = []
                        st.session_state.click_coords.append((click_x, click_y))
                        st.success(f"已新增標記點 ({click_x}, {click_y})")
                
                with col1_2:
                    if st.button("🗑️ 清空標記"):
                        st.session_state.click_coords = []
                        st.session_state.mask_data = None
                        st.info("標記已清空")
                
                # 顯示已標記的點
                if 'click_coords' in st.session_state and st.session_state.click_coords:
                    st.write("已標記的區域座標：")
                    for i, (x, y) in enumerate(st.session_state.click_coords):
                        st.write(f"{i+1}. ({x}, {y})")

            with col2:
                st.subheader("✨ 修復與同步")
                if st.button("🚀 開始 AI 修復"):
                    if 'click_coords' in st.session_state and st.session_state.click_coords:
                        with st.spinner("AI 正在分析並補全..."):
                            # 生成遮罩
                            from PIL import ImageDraw
                            mask = generate_mask_from_click(
                                raw_img, 
                                st.session_state.click_coords, 
                                st.session_state.stroke_width
                            )
                            st.session_state.mask_data = mask
                            
                            # AI修復
                            res_bytes = stable_artifact_repair(raw_img, mask)
                            if res_bytes:
                                st.session_state.result_img = Image.open(io.BytesIO(res_bytes))
                                st.success("修復完成！")
                    else:
                        st.warning("⚠️ 請先標記殘缺區域！")

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
            st.exception(e)

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
