# 圆体字库

本机自托管的中文圆体，不依赖外部字体服务。

- 上游：[Resource Han Rounded](https://github.com/CyanoHao/Resource-Han-Rounded)，基于 Adobe Source Han Sans。
- 版本：[v0.990 稳定版](https://github.com/CyanoHao/Resource-Han-Rounded/releases/tag/v0.990)。输入为 `RHR-CN-0.990.7z` 中的 `ResourceHanRoundedCN-Regular.ttf` 和 `ResourceHanRoundedCN-Bold.ttf`。
- 上游压缩包 SHA-256：`e7005f7b4a7a0b8352d32c4a1358ff47564eb73be7fdb2db00d9f792755e9dc7`。
- 授权：SIL Open Font License 1.1，完整授权及原作者声明见 [OFL-License.txt](OFL-License.txt)。未使用 Windows 系统字体。
- 处理：将稳定版 Regular（原始字重 400）与 Bold（原始字重 700）转为 WOFF2，分别供 CSS 映射为 450 正文与 650 强调样式。文件名中的数值是应用的 CSS 映射，不是对上游字重的声明。保留完整字符映射，不按页面文案裁切。
- 覆盖：30,887 个 Unicode 字符，包括简体中文、拉丁字母、数字及常用标点。上游未包含的生僻字仍由 CSS 中的系统字体回退。
- 复现：安装 `fonttools[woff]`，解压上游 TTF 后运行 `python scripts/build_rounded_fonts.py path/to/extracted-ttf-directory`。

建议 CSS 将正文映射至 450，将加粗映射至 650；`font-display: swap` 保持首屏文字可读。字体家族名可定义为 `Resource Han Rounded`，并追加 `YouYuan, "Microsoft YaHei UI", sans-serif` 回退。

字库体积：正文 4,937,140 字节，加粗 5,040,176 字节，合计约 9.52 MiB。保留完整中文覆盖会增加下载量；两份文件均由本机服务提供，浏览器可按实际使用的字重加载。
