import os


from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFile,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingException,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsWkbTypes,
)
from .compat import FIELD_STRING, FIELD_DOUBLE

from .core import transform_vertices
from .landxml.geometry import read_alignments
from .processing_common import BoundsTracker, coordinate_choices
from .params import number_param

METHODS = [
    "Use stored coordinates",
    "Swap X / Y",
    "Apply X / Y offset",
    "Swap X / Y + offset",
    "2-D Helmert (similarity)",
    "Reproject from source CRS",
]


def _numeric(value):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


METHOD_MAP = {
    "Use stored coordinates": "stored",
    "Swap X / Y": "swap",
    "Apply X / Y offset": "offset",
    "Swap X / Y + offset": "swap_offset",
    "2-D Helmert (similarity)": "helmert",
    "Reproject from source CRS": "reproject",
}


class LandXMLAlignmentsAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    OUTPUT_CRS = "OUTPUT_CRS"
    METHOD = "METHOD"
    DX = "DX"
    DY = "DY"
    ROTATION = "ROTATION"
    SCALE = "SCALE"
    SOURCE_CRS = "SOURCE_CRS"
    ALIGNMENT = "ALIGNMENT"
    SEGMENT = "SEGMENT"
    OUTPUT = "OUTPUT"

    def createInstance(self):
        return LandXMLAlignmentsAlgorithm()

    def name(self):
        return "landxml_alignments_to_vector"

    def displayName(self):
        return "Extract LandXML Alignments to Lines"

    def group(self):
        return "Vector extraction"

    def groupId(self):
        return "vector_extraction"

    def shortHelpString(self):
        return (
            "Extracts LandXML <Alignment> geometry into GIS line features. "
            "Supports line, clothoid spiral, and circular-curve geometry, densification, explicit output CRS, "
            "and the same coordinate interpretation/transformation options used by the TIN converter. "
            "Spirals are approximated at the chosen segment length and reported if unsupported. "
            "Select the output CRS explicitly; LandXML CRS metadata is reference only."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                self.INPUT,
                "LandXML file",
                behavior=QgsProcessingParameterFile.Behavior.File,
                fileFilter="LandXML (*.xml *.landxml)",
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.METHOD,
                "Coordinate interpretation",
                options=METHODS,
                defaultValue=0,
            )
        )
        self.addParameter(
            number_param(self.DX, "Delta X / Easting", 0.0, -1e9, 1e9, decimals=4)
        )
        self.addParameter(
            number_param(self.DY, "Delta Y / Northing", 0.0, -1e9, 1e9, decimals=4)
        )
        self.addParameter(
            number_param(
                self.ROTATION, "Rotation (degrees)", 0.0, -360.0, 360.0, decimals=8
            )
        )
        self.addParameter(
            number_param(self.SCALE, "Scale factor", 1.0, 1e-8, 1000.0, decimals=10)
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.SOURCE_CRS,
                "Source CRS (for reprojection)",
                defaultValue=None,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS, "Output CRS (result layer / raster)", defaultValue=None
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.ALIGNMENT,
                "Alignment name (blank = all)",
                defaultValue="",
                optional=True,
            )
        )
        self.addParameter(
            number_param(
                self.SEGMENT,
                "Maximum curve segment length (source horizontal units)",
                5.0,
                0.01,
                10000.0,
                decimals=3,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Output alignment lines",
                type=QgsProcessing.SourceType.TypeVectorLine,
                defaultValue=None,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        path = self.parameterAsFile(parameters, self.INPUT, context)
        if not path or not os.path.isfile(path):
            raise QgsProcessingException("Input LandXML file does not exist.")
        method, out_crs, _src_crs, transform_params, document = coordinate_choices(
            self, parameters, context, feedback, path
        )
        seg = self.parameterAsDouble(parameters, self.SEGMENT, context)
        alignment_filter = self.parameterAsString(
            parameters, self.ALIGNMENT, context
        ).strip()

        fields = QgsFields()
        fields.append(QgsField("name", FIELD_STRING, len=254))
        fields.append(QgsField("description", FIELD_STRING, len=254))
        fields.append(QgsField("length", FIELD_DOUBLE))
        fields.append(QgsField("sta_start", FIELD_STRING, len=80))
        fields.append(QgsField("sta_end", FIELD_STRING, len=80))
        for name in (
            "source_vendor",
            "source_file",
            "source_name",
            "source_description",
            "alignment_name",
            "geometry_type",
            "direction",
        ):
            fields.append(QgsField(name, FIELD_STRING, len=254))
        for name in (
            "station_start",
            "station_end",
            "radius",
            "curve_length",
            "spiral_length",
        ):
            fields.append(QgsField(name, FIELD_DOUBLE))
        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.Type.LineString,
            out_crs,
        )
        if sink is None:
            raise QgsProcessingException("Could not create output vector layer.")

        try:
            alignments, unsupported = read_alignments(
                path, seg, feedback, feedback.isCanceled
            )
            if alignment_filter:
                alignments = [a for a in alignments if a["name"] == alignment_filter]
            if not alignments:
                raise QgsProcessingException(
                    "No supported alignment line/curve geometry was found."
                )
            transformed_bounds = BoundsTracker()
            for i, a in enumerate(alignments):
                if feedback.isCanceled():
                    raise QgsProcessingException("Extraction cancelled by user.")
                import numpy as np

                arr = np.array([[x, y, 0.0] for x, y in a["points"]], dtype=float)
                arr2 = transform_vertices(arr, method, **transform_params)
                transformed_bounds.add(arr2)
                geom = QgsGeometry.fromPolylineXY(
                    [QgsPointXY(float(x), float(y)) for x, y in arr2[:, :2]]
                )
                f = QgsFeature(fields)
                f.setGeometry(geom)
                try:
                    length_val = float(a["length"])
                except Exception:
                    length_val = None
                segments = a["segments"]
                single = segments[0] if len(segments) == 1 else None
                f.setAttributes(
                    [
                        a["name"],
                        a["desc"],
                        length_val,
                        a["sta_start"],
                        a["sta_end"],
                        document.vendor,
                        os.path.basename(path),
                        a["name"],
                        a["desc"],
                        a["name"],
                        single["geometry_type"] if single else "compound",
                        single["direction"] if single else None,
                        _numeric(a["sta_start"]),
                        _numeric(a["sta_end"]),
                        single["radius"] if single else None,
                        single["curve_length"] if single else None,
                        single["spiral_length"] if single else None,
                    ]
                )
                sink.addFeature(f)
                feedback.setProgress(80 + int(20 * (i + 1) / len(alignments)))
            if unsupported:
                feedback.pushWarning("Some alignment elements were not extracted:")
                for item in unsupported[:50]:
                    feedback.pushWarning(item)
                if len(unsupported) > 50:
                    feedback.pushWarning(f"...and {len(unsupported) - 50} more.")
            feedback.pushInfo(f"Extracted {len(alignments)} alignment(s).")
            transformed_bounds.report(feedback)
        except QgsProcessingException:
            raise
        except Exception as exc:
            raise QgsProcessingException(
                f"Alignment import failed for '{os.path.basename(path)}': {exc}"
            ) from exc
        return {self.OUTPUT: dest_id, "ALIGNMENT_COUNT": len(alignments)}
