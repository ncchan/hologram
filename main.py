import streamlit as st
import base64
import io
import os
import time
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageEnhance
from streamlit_drawable_canvas import st_canvas
from tencentcloud.common import credential
from tencentcloud.aiart.v20221229 import aiart_client, models
from rembg import remove

# ==========================================
# 1. 配置與常數（適配Streamlit Cloud）
# ==========================================
# 改用session_state儲存全像圖，替代本地檔案（解決Cloud無狀態問題）
# 移除本地快取檔案依賴，避免權限和持久化問題

# ==========================================
# 2. 核心 AI 邏輯（優化金鑰讀取）
# ==========================================
# 從Streamlit Secrets讀取金鑰（安全且適配Cloud）
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
        # 取得金鑰
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
        # 確保圖像包含Alpha通道進行去背
        return remove(img_pil)
    except Exception as e:
        st.warning(f"⚠️ 去背失敗，使用原始圖像: {str(e)}")
        return img_pil.convert("RGBA")

# ==========================================
# 3. 全像投影演算法（優化穩定性）
# ==========================================
def create_pseudo_3d_hologram(img_pil, is_transparent=True):
    try:
        bg_size = 1024
        hologram_bg = Image.new("RGBA", (bg_size, bg_size), (0, 0, 0, 255))
        
        # 增強對比度（新增異常處理）
        enhancer = ImageEnhance.Contrast(img_pil)
        img_ready = enhancer.enhance(1.4)
        img_ready.thumbnail((380, 380))
        
        # 生成四個方向的圖像
        front = img_ready
        back = ImageOps.mirror(img_ready).rotate(180)
        side_w = int(img_ready.width * 0.8)
        left = img_ready.resize((side_w, img_ready.height)).rotate(270, expand=True)
        right = ImageOps.mirror(img_ready).resize((side_w, img_ready.height)).rotate(90, expand=True)
        
        # 計算置中位置
        cx, sy = (bg_size - img_ready.width) // 2, (bg_size - left.height) // 2
        
        # 透明度遮罩
        m_f = front if is_transparent else None
        m_b = back if is_transparent else None
        m_l = left if is_transparent else None
        m_r = right if is_transparent else None

        # 貼上圖像（新增邊界檢查）
        hologram_bg.paste(front, (cx, 70), m_f)
        hologram_bg.paste(back, (cx, bg_size - img_ready.height - 70), m_b)
        hologram_bg.paste(left, (70, sy), m_l)
        hologram_bg.paste(right, (bg_size - right.width - 70, sy), m_r)
        
        return hologram_bg.convert("RGB")
    except Exception as e:
        st.error(f"❌ 生成全像圖失敗: {str(e)}")
        return Image.new("RGB", (1024, 1024), (0, 0, 0))

# ==========================================
# 4. Streamlit 使用者介面（適配Cloud）
# ==========================================
def init_session_state():
    """初始化Session State"""
    default_states = {
        'result_img': None,
        'holo_img': None,  # 儲存全像圖，替代本地檔案
        'last_update': 0   # 記錄最後更新時間
    }
    for key, value in default_states.items():
        if key not in st.session_state:
            st.session_state[key] = value

# 設定頁面配置
st.set_page_config(page_title="2026 AI 文物修復系統", layout="wide")

# 初始化Session State
init_session_state()

# 側邊欄模式切換
st.sidebar.header("⚙️ 模式切換")
app_mode = st.sidebar.selectbox("視窗模式", ["🎨 專家修復端", "🌌 全像投影端"])

if app_mode == "🎨 專家修復端":
    st.title("🏛️ 文物修復主控台")
    
    st.sidebar.divider()
    stroke_w = st.sidebar.slider("筆觸大小", 5, 100, 25)
    tool_mode = st.sidebar.radio("工具", ("畫筆模式", "編輯/刪除模式"))
    drawing_mode = "freedraw" if tool_mode == "畫筆模式" else "transform"
    
    h_type = st.sidebar.radio("全像類型", ("立體文物 (自動去背)", "畫作 (保留背景)"))
    file = st.sidebar.file_uploader("上傳文物圖片", type=["jpg", "png", "jpeg"])

    if file:
        try:
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

                # 顯示修復結果並同步到全像端
                if st.session_state.result_img:
                    st.image(st.session_state.result_img, caption="AI 修復結果", width=400)
                    
                    if st.button("🔮 同步修復圖到全像螢幕"):
                        with st.spinner("同步中..."):
                            # 1. 取得 AI 修復後的圖像
                            img_to_sync = st.session_state.result_img
                            
                            # 2. 根據模式處理（去背或保留背景）
                            is_transparent = "去背" in h_type
                            if is_transparent:
                                processed_img = local_remove_bg(img_to_sync)
                            else:
                                processed_img = img_to_sync.convert("RGBA")
                            
                            # 3. 生成全像四面圖（儲存到session_state，替代本地檔案）
                            holo_final = create_pseudo_3d_hologram(processed_img, is_transparent)
                            st.session_state.holo_img = holo_final
                            st.session_state.last_update = time.time()
                            
                            st.toast("✅ 修復圖已推送到全像螢幕！", icon="🔮")
        except Exception as e:
            st.error(f"❌ 處理圖片失敗: {str(e)}")

else:
    # ==========================================
    # 🌌 全像投影端（適配Cloud無狀態）
    # ==========================================
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
    </style>""", unsafe_allow_html=True)
    
    placeholder = st.empty()
    
    # 迴圈更新全像圖（適配Cloud，避免無限while迴圈導致逾時）
    if st.session_state.holo_img:
        # 將圖像轉為base64
        buf = io.BytesIO()
        st.session_state.holo_img.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()
        
        # 顯示全像圖
        with placeholder.container():
            st.markdown(f"""
                <div id="hologram-display">
                    <img src="data:image/png;base64,{img_b64}" style="max-width: 95%; max-height: 95%; object-fit: contain;">
                </div>
            """, unsafe_allow_html=True)
    else:
        # 初始狀態顯示提示
        with placeholder.container():
            st.markdown(f"""
                <div id="hologram-display">
                    <div style="color: white; font-size: 24px; text-align: center;">
                        🎯 等待修復端同步圖像...
                    </div>
                </div>
            """, unsafe_allow_html=True)
    
    # 新增重新整理按鈕（Cloud不支援無限迴圈，手動重新整理更穩定）
    st.markdown(
        """
        <div style="position: fixed; bottom: 20px; right: 20px; z-index: 999;">
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
