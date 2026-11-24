# battery-tray

在 Windows 11 工作列「開始」按鈕左側顯示無線裝置電量。

## 支援裝置

| 裝置 | 連線方式 | 說明 |
|---|---|---|
| Logitech PRO 2 LIGHTSPEED | LIGHTSPEED 接收器 | 透過 HID++ 2.0 讀取，可與 G HUB 同時執行 |
| Sony INZONE Buds（WF-G700N） | USB 發射器 | 顯示左耳、右耳、充電盒電量 |
| Sony DualSense | USB 或藍牙 | 只有 10 段精度，顯示為「約 N%」 |

## 功能

- 面板嵌在工作列裡，會跟著「開始」按鈕移動（工作列置中時，開關程式會讓「開始」左右移）
- 電池顏色：綠（> 30%）、黃（> 15%）、紅、藍（充電中）
- 滑鼠休眠時整段變暗、加上月亮圖示，並保留最後讀值
- 滑鼠移到面板上會顯示各裝置的詳細資訊
- 右鍵 →「結束」關閉程式

## 安裝與執行

需要 Windows 11 和 Python 3.8 以上。

```sh
pip install hidapi pillow
pythonw battery_tray.py
```

用 `pythonw` 執行不會跳出主控台視窗。

## 打包成 exe

```sh
pip install pyinstaller
python -m PyInstaller --onefile --noconsole --name BatteryTray battery_tray.py
```

產出的檔案在 `dist\BatteryTray.exe`。要開機自動啟動，就在 `shell:startup` 資料夾放一個它的捷徑。

## 自我測試

```sh
python battery_tray.py --test
```

用實機擷取的封包測試 INZONE 和 DualSense 的解析邏輯。

## 已知限制

- 只支援工作列圖示置中；靠左對齊時「開始」在最左邊，面板放不下
- 只處理主螢幕的工作列
- 換桌布或切換深淺色後，面板底色不會跟著變，重開程式即可
- Logitech 接收器分不出滑鼠是關機還是休眠，兩種都顯示為休眠
- INZONE 兩耳都放在充電盒裡時，發射器不會回應，會顯示為未連線
