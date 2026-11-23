"""無線裝置電量：嵌在 Windows 工作列「開始」按鈕左側。用 pythonw 執行可隱藏主控台。"""
import ctypes, sys, threading, time
import tkinter as tk
import hid
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageGrab, ImageTk

POLL_SEC = 2
ASLEEP = "asleep"  # 讀取函式回傳：接收器在但裝置沒回應


# ---------- Logitech PRO 2 LIGHTSPEED（HID++ 2.0, Unified Battery 0x1004）----------
def read_logitech():
    d = next((d for d in hid.enumerate(0x46d, 0xc543) if d['usage_page'] == 0xff00 and d['usage'] == 2), None)
    if not d: return None
    h = hid.device(); h.open_path(d['path'])
    try:
        def req(feat_idx, fn, *params):
            pkt = [0x11, 1, feat_idx, (fn << 4) | 0x0A, *params]
            h.write(pkt + [0] * (20 - len(pkt)))
            for _ in range(20):  # G HUB 也在講 HID++，用 software id 0x0A 過濾自己的回覆
                r = h.read(20, 300)
                if not r: return None
                if r[1] == 1 and r[2] == 0xFF: return None  # 錯誤（滑鼠睡眠/關機）
                if r[1] == 1 and r[2] == feat_idx and r[3] == pkt[3]: return r
        r = req(0, 0, 0x10, 0x04)  # IRoot.getFeature(0x1004)
        if not r or not r[4]: return ASLEEP  # ponytail: 關機和休眠在接收器端分不出來，都當休眠
        r = req(r[4], 1)  # get_status
        return (r[4], r[6] in (1, 2), f"{r[4]}%") if r else ASLEEP
    finally:
        h.close()


# ---------- Sony INZONE Buds WF-G700N（USB 發射器 vendor HID 0xFF04）----------
def parse_inzone(r):
    # 回應：02 12 04 ff 0f 00 96 c3 14 04 10 <tid 2B> <stA pctA stB pctB stCase pctCase> <sum>
    if len(r) < 20 or r[1] != 0x12 or r[2] != 0x04 or r[9] != 0x04: return None
    if sum(r[6:19]) & 0xFF != r[19]: return None
    # ponytail: A=左、B=右 依 openinzone 的實機結論；若相反，取一耳出盒看哪邊變 0xFF 再對調
    left, right, case = (None if p == 0xFF else p for p in (r[14], r[16], r[18]))
    buds = [p for p in (left, right) if p is not None]
    if not buds: return None
    fmt = lambda p: "--" if p is None else f"{p}%"
    return min(buds), False, f"L {fmt(left)} / R {fmt(right)} / 盒 {fmt(case)}"


def read_inzone():
    d = next((d for d in hid.enumerate(0x54c, 0xec2) if d['usage_page'] == 0xff04), None)
    if not d: return None
    h = hid.device(); h.open_path(d['path'])
    try:
        body = [0x96, 0xC3, 0x41, 0x04, 0x01, 0x02, 0x00]  # PC→耳機, battery GET, tid=2
        pkt = [0x02, 12, 0x01, 0x00, 0xFC, 8, *body, sum(body) & 0xFF]
        h.write(pkt + [0] * (64 - len(pkt)))
        end = time.time() + 2  # 兩耳都在盒內時發射器不回應 → 逾時當離線
        while time.time() < end:
            r = h.read(64, 500)
            if r and (res := parse_inzone(r)): return res
    finally:
        h.close()


# ---------- Sony DualSense（USB 或藍牙）----------
def parse_dualsense(r):
    if r[0] == 0x31 and len(r) >= 55: b = r[54]   # 藍牙完整報告
    elif r[0] == 0x01 and len(r) >= 54: b = r[53]  # USB
    else: return None
    pct, status = min((b & 0x0F) * 10 + 5, 100), b >> 4  # ponytail: 只有 0~10 級，+5 取區間中值
    return pct, status in (1, 2), f"約 {pct}%" + ("（充電中）" if status == 1 else "（已充飽）" if status == 2 else "")


def read_dualsense():
    d = next(iter(hid.enumerate(0x54c, 0x0ce6)), None)
    if not d: return None
    h = hid.device(); h.open_path(d['path'])
    try:
        for i in range(30):
            r = h.read(78, 200)
            if r and (res := parse_dualsense(r)): return res
            if r and r[0] == 0x01 and i == 0:
                h.get_feature_report(0x05, 41)  # 藍牙簡易模式 → 讀校正資料可切到 0x31 完整報告
    finally:
        h.close()


# ---------- 工作列介面 ----------
# (名稱, 讀取函式, Segoe Fluent Icons 字形：滑鼠 / 耳機 / 手把)
DEVICES = [("PRO 2 LIGHTSPEED", read_logitech, ""),
           ("INZONE Buds", read_inzone, ""),
           ("DualSense", read_dualsense, "")]
results = {}  # name -> (pct, charging, detail, asleep) 或 None（未連線）；輪詢執行緒寫、UI 讀

u32 = ctypes.windll.user32
GWL_STYLE, GWL_EXSTYLE, WS_CHILD, WS_POPUP, WS_EX_LAYERED = -16, -20, 0x40000000, 0x80000000, 0x80000

ICON_FONT = r"C:\Windows\Fonts\SegoeIcons.ttf"
TEXT_FONT = r"C:\Windows\Fonts\seguisb.ttf"
BLUE = (40, 130, 255, 255)
SS = 3  # 超取樣倍率：Pillow 的圓角沒有反鋸齒，放大畫再縮回


def poll(name, reader):
    # 每個裝置一條執行緒：INZONE 逾時（兩耳在盒內）不會拖慢其他裝置
    while True:
        try: res = reader()
        except OSError: res = None  # 裝置被拔或斷線
        if res == ASLEEP:  # 保留最後讀值
            last = results.get(name)
            pct = last and last[0]
            res = (pct, False, "休眠中" + (f"（最後讀值 {pct}%）" if pct is not None else ""), True)
        elif res:
            res = (*res, False)
        results[name] = res
        time.sleep(POLL_SEC)


def render(items, scale, height):
    """items: [(glyph, pct, charging, asleep)] → 高度 height（實體像素）的 RGBA 圖。尺寸以 DIP 計，scale = DPI/96。"""
    d = lambda v: round(v * scale * SS)
    H = height * SS
    icon_f = ImageFont.truetype(ICON_FONT, d(15))
    text_f = ImageFont.truetype(TEXT_FONT, d(14))
    texts = ["--" if pct is None else f"{pct}%" for _, pct, *_ in items]
    seg = [d(16 + 10 + 28 + 8) + round(text_f.getlength(t)) for t in texts]  # 圖示 + 間距 + 電池 + 間距 + 文字
    m, padx, sep = d(5), d(14), d(29)  # m 留給外發光
    W = 2 * (m + padx) + sum(seg) + sep * (len(items) - 1)
    box, r = (m, m, W - m - 1, H - m - 1), d(10)

    glow = Image.new("RGBA", (W, H))
    ImageDraw.Draw(glow).rounded_rectangle(box, r, outline=BLUE, width=d(3))
    img = glow.filter(ImageFilter.GaussianBlur(d(3)))
    g = ImageDraw.Draw(img)
    g.rounded_rectangle(box, r, fill=(14, 20, 34, 245), outline=BLUE, width=d(1.5))

    cy, x = H // 2, m + padx
    for i, ((glyph, pct, charging, asleep), text, w) in enumerate(zip(items, texts, seg)):
        if i:
            g.line((x - sep // 2, cy - d(9), x - sep // 2, cy + d(9)), fill=(70, 82, 105, 255), width=d(1))
        dim = (105, 115, 135, 255)  # 休眠：整段變暗 + 圖示右上角小月亮
        g.text((x + d(8), cy), glyph, font=icon_f, fill=dim if asleep else BLUE, anchor="mm")
        if asleep:
            g.text((x + d(16), cy - d(7)), "", font=ImageFont.truetype(ICON_FONT, d(9)), fill=(250, 200, 70, 255), anchor="mm")
        bx, bw, bh = x + d(26), d(25), d(13)
        frame = dim if asleep else (205, 210, 220, 255)
        g.rounded_rectangle((bx, cy - bh // 2, bx + bw, cy + bh // 2), d(3), outline=frame, width=d(1.5))
        g.rounded_rectangle((bx + bw + d(1), cy - d(3), bx + bw + d(3), cy + d(3)), d(1), fill=frame)
        fill = dim if asleep or pct is None else (80, 190, 255, 255) if charging else (52, 199, 120, 255) if pct > 30 else (250, 190, 40, 255) if pct > 15 else (240, 70, 70, 255)
        if pct is not None:  # 開機時滑鼠就在休眠 → 還沒有讀值
            inset = d(3)
            fw = (bw - 2 * inset) * max(pct, 8) // 100  # 最少留一點，0~7% 也看得到紅色
            g.rounded_rectangle((bx + inset, cy - bh // 2 + inset, bx + inset + fw, cy + bh // 2 - inset), d(1), fill=fill)
        g.text((x + d(62), cy), text, font=text_f, fill=(140, 150, 168, 255) if asleep else (245, 247, 250, 255), anchor="lm")
        x += w + sep
    return img.resize((round(W / SS), height), Image.LANCZOS)


def rect(hwnd):
    r = (ctypes.c_long * 4)(); u32.GetWindowRect(hwnd, r)
    return tuple(r)


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    root.overrideredirect(True)
    panel = tk.Label(root, bd=0, highlightthickness=0)
    panel.pack()

    tip = tk.Toplevel(root); tip.withdraw(); tip.overrideredirect(True); tip.attributes("-topmost", True)
    tip_text = tk.Label(tip, justify="left", font=("Segoe UI", 10), padx=10, pady=6, bg="#0e1422", fg="#f3f3f3",
                        highlightthickness=1, highlightbackground="#2882ff")
    tip_text.pack()

    def show_tip(_):
        tip_text.config(text="\n".join(f"{n}: {results[n][2]}" for n, *_ in DEVICES if results.get(n)))
        tip.update_idletasks()
        _, t, r, _ = rect(hwnd)
        tip.geometry(f"+{r - tip.winfo_reqwidth()}+{t - tip.winfo_reqheight() - 8}")
        tip.deiconify()

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="結束", command=root.destroy)
    panel.bind("<Enter>", show_tip)
    panel.bind("<Leave>", lambda _: tip.withdraw())
    panel.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    root.update_idletasks()
    hwnd = u32.GetParent(root.winfo_id())  # Tk 的外框視窗
    style = (u32.GetWindowLongW(hwnd, GWL_STYLE) & ~WS_POPUP) | WS_CHILD
    u32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(style).value)
    # 一定要是 layered（不透明 LWA_ALPHA 255）：Win11 的 XAML 工作列會蓋掉一般子視窗
    u32.SetWindowLongW(hwnd, GWL_EXSTYLE, u32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED)
    u32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)
    state = {"parent": None, "geo": None, "bg": None, "items": None}

    # Win11 工作列由 XAML 合成：逐像素 alpha（UpdateLayeredWindow）的子視窗不會被畫、頂層視窗又會被工作列蓋住，
    # 所以用不透明子視窗，把面板合成在截下來的工作列背景上
    def capture():
        # 截「開始」左邊整條工作列當背景快取；只在工作列/開始按鈕位置變動時重截，內容變動不會閃
        # ponytail: 換桌布或切深淺色後背景不會跟上，重開程式即可
        ax, h, _, tl, tt = state["geo"]
        state.update(bg=ImageGrab.grab((tl, tt, tl + ax, tt + h)).convert("RGBA"), items=None)

    def draw(items):
        state["items"] = items
        if not items:
            u32.ShowWindow(hwnd, 0); return
        ax, h, dpi, _, _ = state["geo"]
        img = render(items, dpi / 96, h)
        # ponytail: 工作列圖示靠左對齊時「開始」在最左邊、放不下，要改錨點才行
        x = max(ax - img.width - 4, 0)
        bg = state["bg"].crop((x, 0, x + img.width, h))
        bg.alpha_composite(img)
        panel.image = ImageTk.PhotoImage(bg)
        panel.config(image=panel.image)
        u32.MoveWindow(hwnd, x, 0, img.width, h, True)
        u32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE

    def tick():
        # 持續貼齊：工作列尺寸變動或 explorer 重啟後都能自己回來
        # ponytail: 只處理主螢幕工作列，多螢幕要另外列舉 Shell_SecondaryTrayWnd
        tray = u32.FindWindowW("Shell_TrayWnd", None)
        start = u32.FindWindowExW(tray, None, "Start", None)  # 置中工作列時，開啟/關閉程式會讓它左右移
        items = tuple((glyph, results[n][0], results[n][1], results[n][3]) for n, _, glyph in DEVICES if results.get(n))
        if tray and start:
            if state["parent"] != tray:
                u32.SetParent(hwnd, tray); state.update(parent=tray, geo=None)
            tl, tt, _, tb = rect(tray)
            geo = (rect(start)[0] - tl, tb - tt, u32.GetDpiForWindow(tray), tl, tt)
            if geo != state["geo"]:
                state.update(geo=geo, bg=None)
                u32.ShowWindow(hwnd, 0)  # 先藏起來，才截得到乾淨的背景
                root.after(200, capture)
            elif state["bg"] and items != state["items"]:
                draw(items)
        root.after(200, tick)

    for name, reader, _ in DEVICES:
        threading.Thread(target=poll, args=(name, reader), daemon=True).start()
    tick()
    root.mainloop()


def selftest():
    # 實機擷取的 INZONE 回應
    assert parse_inzone(bytes.fromhex("021204ff0f0096c3140410020000640063ff3982"))[0] == 99
    assert parse_inzone(bytes.fromhex("021204ff0f0096c3140410010000ff0064ff6145"))[2].startswith("L -- / R 100%")
    assert parse_inzone(bytes.fromhex("021204ff0f0096c3140410010000ff0064ff6146")) is None  # checksum 錯
    usb = [0x01] + [0] * 63; usb[53] = 0x1A  # 充電中、滿格
    assert parse_dualsense(usb) == (100, True, "約 100%（充電中）")
    bt = [0x31] + [0] * 77; bt[54] = 0x03
    assert parse_dualsense(bt)[0] == 35
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--test" in sys.argv else main()
