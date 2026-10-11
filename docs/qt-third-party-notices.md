# RaceTrace Lab 2.0 第三方通知

本包使用动态 PySide6 / Qt、shiboken6、pyqtgraph 与 NumPy。
确切已收集库见 native-library-inventory.json，发行包许可文本见 licenses/ 和 license-inventory.json。
当前 PySide wheel 的 metadata 声明开源许可选项，但 License-File 只附商业条款；
另附来自 Qt 6.11.2 仓库的未修改 LGPL / GPL / Qt exception 全文及获取来源，不把缺失正文当作已完成法律审核。
PyInstaller 只负责构建，bootloader 有 GPL 特殊分发例外；全文亦附带。
系统 Microsoft YaHei UI / Segoe UI 只在运行电脑调用，没有字体文件分发。
没有包含 FIA / F1 网站地图图片、Google 地图或商业游戏资源。

PySide6 / shiboken 及相应 Qt 开源库具有 LGPLv3 / GPLv3 / 商业许可选项，具体模块及第三方组件以各自文本为准。
本包使用 Widgets / pyqtgraph；已收集的 Qt 动态模块为 Core、Gui、Network、OpenGL、OpenGLWidgets、Svg、Test、Widgets 及相应平台 / 图像 / TLS 插件，不含 Qt Graphs 或 WebEngine。准确文件清单以 Binaries.json 为准。
保留 `_internal` 中动态库的可替换性，不限制用户依法调试修改这些库。
pyqtgraph 是 MIT；NumPy 及其内含组件按所附 BSD / 第三方许可通知分发。
项目自身许可未在原本地根目录提供，本次不擅自设定应用源码许可。

来源与版本定位：

- Qt 许可与第三方 / SBOM 索引：https://doc.qt.io/qt-6.11/licensing.html
- Qt for Python 许可：https://doc.qt.io/qtforpython-6/licenses.html
- Qt 6.11.2 对应源码索引：https://download.qt.io/archive/qt/6.11/6.11.2/
- PySide / shiboken 6.11.2 源码索引：https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/
- pyqtgraph 0.14.0：https://github.com/pyqtgraph/pyqtgraph/tree/pyqtgraph-0.14.0
- NumPy 2.5.3：https://github.com/numpy/numpy/tree/v2.5.3

Licenses.json 列出收集的分发包、版本、许可文件短名、原文件名和 SHA-256；licenses/Qt-upstream/upstream-provenance.json 列出补充正文的版本化来源和哈希。缩短的是路径，不是许可正文。
上面的 Qt / PySide 源码目录提供对应版本源码取得入口；应用源码、锁定依赖及构建脚本随同发布。用户可替换 _internal 中动态库，并依法调试所修改的这些库；本项目不附加相反限制。
Python 及 NumPy 内含原生组件的通知保留在所附许可目录。应用自身的源码许可尚未选择，不替代或限制第三方原有许可权利。
清单和来源链接不是完整法律意见，不宣称已完成独立法律审计。
