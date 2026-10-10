# Z30 C 1.20 支持说明

Z7 C 3.80 是当前主要目标；Z30 C 1.20 的支持仍然保留。

## 输入

```text
Z_30_0120.bin
size   34496798
sha256 1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a
```

提取后的 EG 模块：

```text
eg2090_0120a0.bi
size   33939514
sha256 8a99ced2d8220729ba067587b0624bf30c81757abc5d728768134b1c7fd457fa
```

## 构建

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python patch.py full --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

也可使用：

```text
liveview-dual
m900
shutter-sound
full
```

## 声音路径

Z30 使用 SD 卡：

```text
A:\SHUTTER.WAV
A:\SHUTTER\1.WAV ... 4.WAV
```

声音格式仍为 8000 Hz、单声道、16 位 PCM、最长 1.08 秒。

## 验证

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
python tools/verify_liveview_gates.py --source "Z_30_0120.bin" --model z30 --preset full
python tools/verify_extended_features.py --source "Z_30_0120.bin"
```

`verify_extended_features.py` 目前只面向 Z30。
