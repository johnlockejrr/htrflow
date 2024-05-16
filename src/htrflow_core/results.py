from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any, Callable, Iterable, Optional, Sequence

import numpy as np

from htrflow_core.utils import geometry, imgproc
from htrflow_core.utils.geometry import Bbox, Mask, Polygon


class Segment:
    """Segment class

    Class representing a segment of an image, typically a result from
    a segmentation model or a detection model.

    Attributes:
        bbox: The bounding box of the segment
        mask: The segment's mask, if available. The mask is stored
            relative to the bounding box. Use the `global_mask()`
            method to retrieve the mask relative to the original image.
        score: Segment confidence score, if available.
        class_label: Segment class label, if available.
        polygon: An approximation of the segment mask, relative to the
            original image. If no mask is available, `polygon` defaults
            to a polygon representation of the segment's bounding box.
        orig_shape: The shape of the orginal input image.
    """

    bbox: Bbox
    mask: Mask | None
    score: float | None
    class_label: str | None
    polygon: Polygon
    orig_shape: tuple[int, int] | None

    def __init__(
        self,
        bbox: tuple[int, int, int, int] | Bbox | None = None,
        mask: Mask | None = None,
        score: float | None = None,
        class_label: str | None = None,
        polygon: Polygon | Sequence[tuple[int, int]] | None = None,
        orig_shape: tuple[int, int] | None = None,
    ):
        """Create a `Segment` instance

        A segment can be created from a bounding box, a polygon, a mask
        or any combination of the three.

        Arguments:
            bbox: The segment's bounding box, as either a `geometry.Bbox`
                instance or as a (xmin, ymin, xmax, ymax) tuple. Required
                if `mask` and `polygon` are None. Defaults to None.
            mask: The segment's mask relative to the original input image.
                Required if both `polygon` and `bbox` are None. Defaults
                to None.
            score: Segment confidence score. Defaults to None.
            class_label: Segment class label. Defaults to None.
            polygon: A polygon defining the segment, relative to the input
                image. Defaults to None. Required if both `mask` and `bbox`
                are None.
            orig_shape: The shape of the orginal input image. Defaults to
                None.
        """

        # Convert polygon and bbox to Polygon and Bbox instances
        if polygon is not None:
            polygon = geometry.Polygon(polygon)
        if bbox is not None:
            bbox = geometry.Bbox(*bbox)

        match (bbox, mask, polygon):
            case (None, None, None):
                raise ValueError("Cannot create a Segment without bbox, mask or polygon")

            case (_, None, None):
                # Only bbox is given: Leave the polygon and mask as None.
                pass

            case (_, _, None):
                # Mask (and possibly bbox) is given: The mask is assumed to be aligned
                # with the original image. The bounding box is discarded (if given) and
                # recomputed from the mask. A polygon is also inferred from the mask.
                # The mask is then converted to a local mask.
                bbox = geometry.mask2bbox(mask)
                polygon = geometry.mask2polygon(mask)
                mask = imgproc.crop(mask, bbox)

            case (None, None, _):
                # Only polygon is given: Create a bounding box from the polygon and
                # leave the mask as None.
                bbox = geometry.Polygon(polygon).bbox()

        self.bbox = bbox
        self.polygon = polygon
        self.mask = mask
        self.score = score
        self.class_label = class_label
        self.orig_shape = orig_shape

    def __str__(self):
        return f"Segment(class_label={self.class_label}, score={self.score}, bbox={self.bbox}, polygon={self.polygon}, mask={self.mask})"  # noqa: E501

    @property
    def global_mask(self, orig_shape: tuple[int, int] | None = None) -> Optional[Mask]:
        """
        The segment mask relative to the original input image.

        Arguments:
            orig_shape: Pass this argument to use another original shape
                than the segment's `orig_shape` attribute. Defaults to None.
        """
        if self.mask is None:
            return None

        orig_shape = self.orig_shape if orig_shape is None else orig_shape
        if orig_shape is None:
            raise ValueError("Cannot compute the global mask without knowing the original shape.")

        x1, y1, x2, y2 = self.bbox
        mask = np.zeros(orig_shape, dtype=np.uint8)
        mask[y1:y2, x1:x2] = self.mask
        return mask

    def approximate_mask(self, ratio):
        """A lower resolution version of the global mask

        Arguments:
            downscale: Size of approximate mask relative to the original.
        """
        return imgproc.rescale(self.global_mask, ratio)

    @property
    def local_mask(self):
        """The segment mask relative to the bounding box (alias for self.mask)"""
        return self.mask

    def rescale(self, factor):
        if self.mask is not None:
            self.mask = imgproc.rescale_linear(self.mask, factor)
        self.bbox = self.bbox.rescale(factor)
        if self.polygon is not None:
            self.polygon = self.polygon.rescale(factor)


@dataclass
class RecognizedText:
    """Recognized text class

    This class represents a result from a text recognition model.

    Attributes:
        texts: A sequence of candidate texts
        scores: The scores of the candidate texts
    """

    texts: Sequence[str]
    scores: Sequence[float]

    def top_candidate(self) -> str:
        """The candidate with the highest confidence score"""
        return self.texts[self.scores.index(self.top_score())]

    def top_score(self):
        """The highest confidence score"""
        return max(self.scores)


@dataclass
class Result:
    metadata: dict[str, str] = field(default_factory=dict)
    segments: Sequence[Segment] = field(default_factory=list)
    texts: Sequence[RecognizedText] = field(default_factory=list)
    data: Sequence[dict[str, Any]] = field(default_factory=list)

    def rescale(self, factor):
        for segment in self.segments:
            segment.rescale(factor)

    @property
    def bboxes(self) -> Sequence[Bbox]:
        """Bounding boxes relative to input image"""
        return [segment.bbox for segment in self.segments]

    @property
    def global_masks(self) -> Sequence[Mask]:
        """Global masks relative to input image"""
        return [segment.global_mask for segment in self.segments]

    @property
    def local_mask(self) -> Sequence[Mask]:
        """Local masks relative to bounding boxes"""
        return [segment.local_mask for segment in self.segments]

    @property
    def polygons(self) -> Sequence[Polygon]:
        """Polygons relative to input image"""
        return [segment.polygon for segment in self.segments]

    @property
    def class_labels(self) -> Sequence[str]:
        """Class labels of segments"""
        return [segment.class_label for segment in self.segments]

    @classmethod
    def text_recognition_result(cls, metadata: dict, text: RecognizedText) -> "Result":
        """Create a text recognition result

        Arguments:
            image: The original image
            metadata: Result metadata
            text: The recognized text

        Returns:
            A Result instance with the specified data and no segments.
        """
        return cls(metadata, data=[{"text_result": text}])

    @classmethod
    def segmentation_result(
        cls,
        orig_shape: tuple[int, int],
        metadata: dict,
        bboxes: Sequence[Bbox] | np.ndarray = None,
        masks: np.ndarray | None = None,
        polygons: Sequence[Polygon] | None = None,
        scores: Iterable[float] | None = None,
        labels: Iterable[str] | None = None,
    ) -> "Result":
        """Create a segmentation result

        Arguments:
            image: The original image
            metadata: Result metadata
            segments: The segments

        Returns:
            A Result instance with the specified data and no texts.
        """
        segments = []
        for item in _zip_longest_none(bboxes, masks, scores, labels, polygons):
            segments.append(Segment(*item, orig_shape=orig_shape))
        return cls(metadata, segments=segments)

    def reorder(self, index: Sequence[int]) -> None:
        """Reorder result

        Example: Given a `Result` with three segments s0, s1 and s2,
        index = [2, 0, 1] will put the segments in order [s2, s0, s1].
        Any indices not in `index` will be dropped from the result.

        Arguments:
            index: A list of indices representing the new ordering.
        """
        if self.segments:
            self.segments = [self.segments[i] for i in index]
        if self.data:
            self.data = [self.data[i] for i in index]

    def drop_indices(self, index: Sequence[int]) -> None:
        """Drop segments from result

        Example: Given a `Result` with three segments s0, s1 and s2,
        index = [0, 2] will drop segments s0 and s2.

        Arguments:
            index: Indices of segments to drop
        """
        keep = [i for i in range(len(self.segments)) if i not in index]
        self.reorder(keep)

    def filter(self, key: str, predicate: Callable[[Any], bool]) -> None:
        """Filter segments and data based on a predicate applied to a specified key.

        Args:
            key: The key in the data dictionary to test the predicate against.
            predicate [Callable]: A function that takes a value associated with the key
            and returns True if the segment should be kept.

        Example:
        ```
        >>> def remove_certain_text(text_results):
        >>>    return text_results != 'lorem'
        >>> result.filter('text_results', remove_certain_text)
        True
        ```
        """
        keep = [i for i, item in enumerate(self.data) if predicate(item.get(key, None))]
        self.reorder(keep)


def _zip_longest_none(*items: Iterable[Iterable[Any] | None]):
    """zip_longest() but treats None as an empty list"""
    return zip_longest(*[[] if item is None else item for item in items])
