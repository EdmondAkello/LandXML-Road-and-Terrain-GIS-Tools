"""Processing parameters whose dropdown lists names from the chosen LandXML file.

The parameter stays a plain string, so scripts, models and batch runs keep
working; in the dialog it is an editable dropdown refreshed whenever the
LandXML file parameter changes. Leaving it blank means "detect".
"""

from __future__ import annotations

import os

from qgis.core import QgsProcessingParameterString

from .landxml.catalog import read_catalog

try:  # The Processing plugin's GUI wrappers are absent in some headless runs.
    from processing.gui.wrappers import WidgetWrapper
except ImportError:  # pragma: no cover - depends on the runtime
    WidgetWrapper = None


def name_param(name, description, kind, default="", optional=True, file_param="INPUT", fallback_file_param=None):
    parameter = QgsProcessingParameterString(name, description, defaultValue=default, optional=optional)
    if WidgetWrapper is not None:
        parameter.setMetadata(
            {"widget_wrapper": {"class": LandXMLNameWidgetWrapper, "kind": kind, "file_param": file_param,
                                "fallback_file_param": fallback_file_param}}
        )
    return parameter


def names_for(path, kind):
    if not path or not os.path.isfile(path):
        return []
    try:
        return read_catalog(path).names(kind)
    except (OSError, ValueError):
        return []


if WidgetWrapper is not None:
    from qgis.PyQt.QtWidgets import QComboBox

    class LandXMLNameWidgetWrapper(WidgetWrapper):
        def createWidget(self, kind="surfaces", file_param="INPUT", fallback_file_param=None, **_kwargs):
            self._kind = kind
            self._file_param = file_param
            self._fallback_file_param = fallback_file_param
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.lineEdit().setPlaceholderText("(blank = detect automatically)")
            combo.currentTextChanged.connect(lambda _text: self.widgetValueHasChanged.emit(self))
            return combo

        def postInitialize(self, wrappers):
            self._sources = {wrapper.parameterDefinition().name(): wrapper for wrapper in wrappers
                             if wrapper.parameterDefinition().name() in (self._file_param, self._fallback_file_param)}
            for wrapper in self._sources.values():
                wrapper.widgetValueHasChanged.connect(lambda changed: self.refresh(changed))
            self.refresh(self._sources.get(self._file_param))

        def refresh(self, wrapper):
            path = ""
            for key in (self._file_param, self._fallback_file_param):
                source = getattr(self, "_sources", {}).get(key) or wrapper
                if source is None:
                    continue
                try:
                    value = source.parameterValue()
                except AttributeError:
                    value = source.value()
                if isinstance(value, str) and value:
                    path = value
                    break
            combo = self.widget
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("")
            combo.addItems(names_for(path, self._kind))
            combo.setEditText(current)
            combo.blockSignals(False)

        def setValue(self, value):
            self.widget.setEditText("" if value is None else str(value))

        def value(self):
            return self.widget.currentText().strip()
else:  # pragma: no cover
    LandXMLNameWidgetWrapper = None
