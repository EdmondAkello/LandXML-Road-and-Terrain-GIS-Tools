"""Small Qt/QGIS compatibility surface shared by QGIS 3.44 and 4.2."""

from qgis.PyQt.QtCore import QMetaType

FIELD_STRING = QMetaType.Type.QString
FIELD_DOUBLE = QMetaType.Type.Double
FIELD_INT = QMetaType.Type.Int
