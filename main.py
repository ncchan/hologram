import streamlit as st
import base64
import io
import time
import os
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageEnhance, ImageDraw
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
# 改用通用的client和request（兼容所有版本）
from tencentcloud.aiart.v20221229 import aiart_client
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from rembg import remove, new_session
import matplotlib.pyplot as plt
import cv2
from streamlit_drawable_canvas import st_canvas

# ==========================================
# 1. 全域配置與快取檔案路徑
# ==========================================
TEMP_FILE_PATH = "_temp_holo.png"  # 用於跨分頁同步的臨時檔案
st.set_page_config(page_title="2026 AI 文物修復系統", layout="wide")
plt.switch_backend('Agg')

# ==========================================
# 2. 跨分頁同步工具 (核心修復)
# ==========================================
def save_to_hologram(img_pil):
    """將圖片保存到臨時檔案，供投影端讀取"""
    try:
        img_pil.save(TEMP_FILE_PATH)
        return True
    except Exception as e:
        st.error(f"❌ 同步失敗: {str(e)}")
        return False

def load_hologram():
    """從臨時檔案讀取最新的全息圖"""
    if os.path.exists(TEMP_FILE_PATH):
        try:
            return Image.open(TEMP_FILE_PATH)
        except:
            return None
    return None

# ==========================================
# 3. 核心 AI 與圖像處理 (修復腾讯云接口)
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

def stable_artifact_repair(img_pil, mask_pil):
    """修復版AI修復：兼容腾讯云接口 + 本地模拟"""
    try:
        SECRET_ID, SECRET_KEY = get_credentials()
        
        # 本地模拟模式（无密钥/接口错误时兜底）
        if not SECRET_ID or not SECRET_KEY:
            st.info("ℹ️ 演示模式：生成智能模糊修復效果")
            # 优化模拟效果：仅模糊标记区域
            img_array = np.array(img_pil)
            mask_array = np.array(mask_pil) / 255.0
            blurred = cv2.GaussianBlur(img_array, (15,15), 0)
            # 仅替换标记区域
            result_array = img_array * (1 - mask_array[:, :, np.newaxis]) + blurred * mask_array[:, :, np.newaxis]
            result_img = Image.fromarray(result_array.astype(np.uint8))
            buf = io.BytesIO()
            result_img.save(buf, format="PNG")
            return buf.getvalue()

        # 腾讯云接口调用（通用版，兼容所有SDK版本）
        cred = credential.Credential(SECRET_ID, SECRET_KEY)
        
        # 配置HTTP和客户端
        httpProfile = HttpProfile()
        httpProfile.endpoint = "aiart.tencentcloudapi.com"
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile
        client = aiart_client.AiartClient(cred, "ap-guangzhou", clientProfile)
        
        # 图片转Base64
        def to_b64(image):
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        
        # 处理遮罩（模糊优化）
        mask_blur = mask_pil.filter(ImageFilter.GaussianBlur(radius=3))
        
        # 通用请求参数（适配所有图像修复接口）
        params = {
            "TaskType": "ImageInpainting",  # 图像修复任务类型
            "Image": to_b64(img_pil),
            "Mask": to_b64(mask_blur),
            "Resolution": "720p"  # 输出分辨率
        }
        
        # 发送请求并获取结果
        resp = client.call("CreateImageInpaintingTask", params)
        if resp and "ResultImage" in resp:
            return base64.b64decode(resp["ResultImage"])
        else:
            st.warning("⚠️ 腾讯云接口返回无结果，使用本地模拟修复")
            # 降级到本地模拟
            img_blur = img_pil.filter(ImageFilter.GaussianBlur(3))
            buf = io.BytesIO()
            img_blur.save(buf, format="PNG")
            return buf.getvalue()
            
    except TencentCloudSDKException as e:
        st.error(f"❌ 腾讯云API錯誤: {str(e)}")
        # 腾讯云接口失败，降级到本地模拟
        img_blur = img_pil.filter(ImageFilter.GaussianBlur(3))
        buf = io.BytesIO()
        img_blur.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        st.error(f"❌ AI 修復錯誤: {str(e)}")
        # 所有错误都降级到本地模拟
        buf = io.BytesIO()
        img_pil.save(buf, format="PNG")
        return buf.getvalue()

def local_remove_bg(img_pil):
    try:
        session = new_session("isnet-general-use")
        return remove(img_pil, session=session)
    except Exception as e:
        st.warning(f"⚠️ AI去背失敗，使用顏色去背: {str(e)}")
        # 備用方案：白色變透明
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
# 4. 進階畫布工具 (支援橡皮擦)
# ==========================================
def draw_on_image_advanced(img_pil, stroke_w):
    st.subheader("🖍️ 標記殘缺區域")
    
    # 模式切換
    col1, col2 = st.columns(2)
    with col1:
        mode = st.radio("工具模式", ["✏️ 繪製 (標記)", "🧽 橡皮擦 (修正)"], key="tool_mode")
    
    # 設置顏色：繪製為紅色，橡皮擦為透明
    stroke_color = "#FF0000" if mode == "✏️ 繪製 (標記)" else "#00000000"
    
    max_size = 800
    width, height = img_pil.size
    if width > max_size or height > max_size:
        ratio = min(max_size/width, max_size/height)
        new_size = (int(width*ratio), int(height*ratio))
        img_pil = img_pil.resize(new_size, Image.Resampling.LANCZOS)

    # 繪製畫布
    canvas_result = st_canvas(
        fill_color="rgba(255, 255, 255, 0.0)",
        stroke_width=stroke_w,
        stroke_color=stroke_color,
        background_image=img_pil,
        update_streamlit=True,
        height=img_pil.height,
        width=img_pil.width,
        drawing_mode="freedraw",
        key="advanced_canvas",
    )

    mask_img = None
    if canvas_result.image_data is not None:
        mask_np = canvas_result.image_data[:, :, 0]
        mask_np = (mask_np > 0).astype(np.uint8) * 255
        mask_img = Image.fromarray(mask_np)
        st.session_state.mask_img = mask_img
        
        # 預覽
        col1, col2 = st.columns(2)
        with col1: st.image(img_pil, caption="原始圖片", use_column_width=True)
        with col2: st.image(mask_img, caption="修復遮罩 (白色區域)", use_column_width=True)
    
    return mask_img

# ==========================================
# 5. 主程式流程
# ==========================================
def init_session_state():
    default_states = {
        'result_img': None, 'holo_img': None, 'uploaded_img': None, 
        'mask_img': None, 'stroke_width': 25
    }
    for k, v in default_states.items():
        if k not in st.session_state: st.session_state[k] = v

init_session_state()

st.sidebar.header("⚙️ 系統選單")
app_mode = st.sidebar.selectbox("視窗模式", ["🎨 專家修復端", "🌌 全像投影端"])

if app_mode == "🎨 專家修復端":
    st.title("🏛️ 文物修復主控台")
    
    st.sidebar.divider()
    st.session_state.stroke_width = st.sidebar.slider("筆刷粗細", 5, 100, 25)
    h_type = st.sidebar.radio("全像類型", ("立體文物 (自動去背)", "畫作 (保留背景)"))
    file = st.sidebar.file_uploader("上傳文物圖片", type=["jpg", "png", "jpeg"])

    if file:
        try:
            raw_img = Image.open(file).convert("RGB")
            st.session_state.uploaded_img = raw_img
            display_img = raw_img.resize((600, int(raw_img.height * 600 / raw_img.width)))
            
            col1, col2 = st.columns(2)
            with col1:
                mask_img = draw_on_image_advanced(display_img, st.session_state.stroke_width)

            with col2:
                st.subheader("✨ 修復與同步")
                
                if st.button("🚀 開始 AI 修復"):
                    with st.spinner("AI 正在分析..."):
                        if not st.session_state.mask_img:
                            st.warning("請先在左側標記修復區域！")
                            continue
                        
                        res_bytes = stable_artifact_repair(raw_img, st.session_state.mask_img)
                        if res_bytes:
                            st.session_state.result_img = Image.open(io.BytesIO(res_bytes))
                            st.success("✅ 修復完成！")

                if st.session_state.result_img:
                    st.image(st.session_state.result_img, caption="AI 修復結果", width=400)
                    
                    # 同步按鈕：保存到檔案
                    if st.button("🔮 同步修復圖到全像螢幕", type="primary"):
                        with st.spinner("正在廣播圖像..."):
                            img_to_sync = st.session_state.result_img
                            is_transparent = "去背" in h_type
                            
                            if is_transparent:
                                processed_img = local_remove_bg(img_to_sync)
                            else:
                                processed_img = img_to_sync.convert("RGBA")
                            
                            holo_final = create_pseudo_3d_hologram(processed_img, is_transparent)
                            
                            # 核心修改：保存到檔案
                            if save_to_hologram(holo_final):
                                st.session_state.holo_img = holo_final
                                st.toast("📡 圖像已同步至投影端！", icon="✅")

        except Exception as e:
            st.error(f"❌ 處理失敗: {str(e)}")

else:
    # 🌌 全像投影端 (自動刷新)
    st.markdown("""<style>
        [data-testid="stSidebar"] {display: none;}
        footer {visibility: hidden;}
        body { background-color: black; }
        #hologram-display { 
            height: 100vh; width: 100vw; 
            display: flex; align-items: center; justify-content: center; 
            position: fixed; top: 0; left: 0; background: black;
        }
    </style>""", unsafe_allow_html=True)
    
    # 自動刷新機制（每2秒刷新一次）
    st.markdown("""
        <meta http-equiv="refresh" content="2">
    """, unsafe_allow_html=True)
    
    placeholder = st.empty()
    img = load_hologram()
    
    if img:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        
        with placeholder.container():
            st.markdown(f"""
                <div id="hologram-display">
                    <img src="data:image/png;base64,{img_b64}" style="max-height: 90vh; border: 2px solid #00ff00;">
                </div>
            """, unsafe_allow_html=True)
    else:
        with placeholder.container():
            st.markdown(f"""
                <div id="hologram-display">
                    <div style="color: #00ff00; font-size: 24px; text-shadow: 0 0 10px #00ff00;">
                        📡 等待修復端同步...
                    </div>
                </div>
            """, unsafe_allow_html=True)
