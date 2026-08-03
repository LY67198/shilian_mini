"""backoffice 岗位模板 schema category 枚举测试"""
import pytest
from pydantic import ValidationError


@pytest.mark.unit
class TestPositionTemplateCategory:
    def test_accepts_custom_category(self):
        from app.schemas.backoffice.position_template import PositionTemplateCreate
        t = PositionTemplateCreate(position_tag="custom_x", title="自定义岗位", category="custom")
        assert t.category == "custom"

    def test_rejects_unknown_category(self):
        from app.schemas.backoffice.position_template import PositionTemplateCreate
        with pytest.raises(ValidationError):
            PositionTemplateCreate(position_tag="x", title="X", category="other")
