import os
import re
import sys
import shutil
import argparse

# ==============================================================================
# NAP_ANH_DONG_LOAT.PY (Chuan trang si Havana - Toi uu anh net muot cho Mobile)
# Cach dung (Neu muon xu ly hang tram anh/video tren may tinh truoc khi up GitHub):
#   1. Bo anh/video/bang size (ten file co chua Ma SKU, VD: CV593_1.jpg, AD251.mp4, CV593_size.jpg)
#      vao thu muc "input_media/"
#   2. Chay: python nap_anh_dong_loat.py
#      (Ho tro tuy chon: --only-image | --only-video | --only-size)
#   3. Keo tha thu muc images/, videos/, sizes/ len kho GitHub havana-sale!
# ==============================================================================

SKU_EXTRACT_RX = re.compile(r"([A-Z]{1,5}\d{1,5})", re.I)


def optimize_image(src_path: str, dst_path: str, max_dim: int = 1600):
    try:
        from PIL import Image, ImageFilter, ImageOps
        with Image.open(src_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            w, h = im.size
            # Neu anh nho (< 900px), upscale nhe bang Lanczos + UnsharpMask cho net cang khi Zoom
            if max(w, h) < 900:
                scale = 1200.0 / max(w, h)
                im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=115, threshold=3))
            elif max(w, h) > max_dim:
                # Thu gon kich thuoc vua khit 2K de load tren Zalo / 4G sieu muot (<180KB/anh)
                scale = float(max_dim) / max(w, h)
                im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                im = im.filter(ImageFilter.UnsharpMask(radius=0.9, percent=95, threshold=3))

            if im.mode == "RGBA":
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[3])
                im = bg
            im.save(dst_path, format="WEBP", quality=86, method=4)
            return True
    except ImportError:
        shutil.copy2(src_path, dst_path)
        return False
    except Exception as e:
        print(f"[!] Copy nguyen goc {src_path} ({e})")
        shutil.copy2(src_path, dst_path)
        return False


def main():
    parser = argparse.ArgumentParser(description="Nap anh, video, bang size dong loat chuan Havana")
    parser.add_argument("--input", default="input_media", help="Thu muc chua anh/video goc can nap")
    parser.add_argument("--only-image", action="store_true", help="Chi xu ly anh san pham")
    parser.add_argument("--only-video", action="store_true", help="Chi xu ly video san pham")
    parser.add_argument("--only-size", action="store_true", help="Chi xu ly bang size")
    args = parser.parse_args()

    process_all = not (args.only_image or args.only_video or args.only_size)
    do_img = process_all or args.only_image
    do_vid = process_all or args.only_video
    do_sz = process_all or args.only_size

    os.makedirs(args.input, exist_ok=True)
    os.makedirs("images", exist_ok=True)
    os.makedirs("videos", exist_ok=True)
    os.makedirs("sizes", exist_ok=True)

    cnt_img, cnt_vid, cnt_sz = 0, 0, 0
    for root, _, files in os.walk(args.input):
        folder_name = os.path.basename(root).upper()
        folder_sku_m = SKU_EXTRACT_RX.search(folder_name) if root != args.input else None
        folder_sku = folder_sku_m.group(1).upper() if folder_sku_m else ""

        for idx, fname in enumerate(sorted(files), 1):
            if fname.startswith("."):
                continue
            src = os.path.join(root, fname)
            low = fname.lower()
            m = SKU_EXTRACT_RX.search(fname.upper())
            sku = m.group(1).upper() if m else folder_sku
            if not sku:
                print(f"[!] Bo qua '{fname}' vi khong tim thay Ma SKU (VD: CV593, AD251, PK1032) trong ten file/thu muc!")
                continue

            if low.endswith((".mp4", ".mov", ".webm")) and do_vid:
                ext = os.path.splitext(low)[1]
                dst = os.path.join("videos", f"{sku}{ext}")
                shutil.copy2(src, dst)
                cnt_vid += 1
            elif low.endswith((".jpg", ".jpeg", ".png", ".webp")):
                is_size = ("size" in low or "bang_size" in low or "bangsize" in low)
                if is_size and do_sz:
                    dst = os.path.join("sizes", f"{sku}_size.webp")
                    if not optimize_image(src, dst, max_dim=1800):
                        dst = os.path.join("sizes", f"{sku}_size" + os.path.splitext(low)[1])
                        shutil.copy2(src, dst)
                    cnt_sz += 1
                elif (not is_size) and do_img:
                    stem = os.path.splitext(fname)[0]
                    clean_stem = sku if sku in stem.upper() else f"{sku}_{idx:02d}"
                    if clean_stem.upper() == sku:
                        clean_stem = f"{sku}_{idx:02d}"
                    dst = os.path.join("images", f"{clean_stem}.webp")
                    if not optimize_image(src, dst, max_dim=1500):
                        dst = os.path.join("images", f"{clean_stem}" + os.path.splitext(low)[1])
                        shutil.copy2(src, dst)
                    cnt_img += 1

    print(f"[HOAN TAT] Da xu ly: {cnt_img} anh (images/), {cnt_vid} video (videos/), {cnt_sz} bang size (sizes/)!")
    print("-> Tiep theo: Keo tha thu muc images/, videos/, sizes/ len kho GitHub havana-sale de tu dong cap nhat web!")


if __name__ == "__main__":
    main()
