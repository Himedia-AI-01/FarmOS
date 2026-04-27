"""농약 검색/매칭용 모델 (bootstrap 스키마와 동일)."""

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PesticideSourceProduct(Base):
    """`rag_pesticide_products`."""

    __tablename__ = "rag_pesticide_products"

    product_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    product_code: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    registration_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    ingredient_or_formulation_name: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True
    )
    pesticide_name_eng: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    corporation_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    pesticide_category_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_purpose_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    formulation_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_registered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    registration_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    registration_standard: Mapped[str | None] = mapped_column(Text, nullable=True)
    manufacturer_importer_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    representative_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_registration_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    business_registration_event_name: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)

    applications: Mapped[list["PesticideApplication"]] = relationship(
        back_populates="product"
    )


class PesticideCrop(Base):
    """`rag_pesticide_crops`."""

    __tablename__ = "rag_pesticide_crops"
    __table_args__ = (
        UniqueConstraint(
            "crop_name_normalized", name="uq_rag_pesticide_crops_crop_name_normalized"
        ),
    )

    crop_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crop_name: Mapped[str] = mapped_column(Text, nullable=False)
    crop_name_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)

    applications: Mapped[list["PesticideApplication"]] = relationship(
        back_populates="crop"
    )


class PesticideTarget(Base):
    """`rag_pesticide_targets`."""

    __tablename__ = "rag_pesticide_targets"
    __table_args__ = (
        UniqueConstraint(
            "target_name_normalized",
            "target_kind",
            name="uq_rag_pesticide_targets_normalized_kind",
        ),
    )

    target_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_name: Mapped[str] = mapped_column(Text, nullable=False)
    target_name_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)

    applications: Mapped[list["PesticideApplication"]] = relationship(
        back_populates="target"
    )


class PesticideApplication(Base):
    """`rag_pesticide_product_applications`."""

    __tablename__ = "rag_pesticide_product_applications"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "crop_id",
            "target_id",
            name="uq_rag_pesticide_product_applications_triplet",
        ),
    )

    application_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("rag_pesticide_products.product_id"),
        nullable=False,
        index=True,
    )
    crop_id: Mapped[int] = mapped_column(
        ForeignKey("rag_pesticide_crops.crop_id"),
        nullable=False,
        index=True,
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("rag_pesticide_targets.target_id"),
        nullable=False,
        index=True,
    )
    application_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_timing: Mapped[str | None] = mapped_column(Text, nullable=True)
    dilution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    dilution_factor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_quantity: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_use_count_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_use_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    test_drug_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_livestock_toxicity: Mapped[str | None] = mapped_column(Text, nullable=True)
    ecotoxicity: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)

    product: Mapped[PesticideSourceProduct] = relationship(back_populates="applications")
    crop: Mapped[PesticideCrop] = relationship(back_populates="applications")
    target: Mapped[PesticideTarget] = relationship(back_populates="applications")
    document_entry: Mapped["PesticideProduct | None"] = relationship(
        back_populates="details"
    )


class PesticideProduct(Base):
    """`rag_pesticide_documents`."""

    __tablename__ = "rag_pesticide_documents"
    __table_args__ = (
        UniqueConstraint(
            "application_id", name="uq_rag_pesticide_documents_application_id"
        ),
    )

    document_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("rag_pesticide_product_applications.application_id"),
        nullable=False,
        index=True,
    )
    crop_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    crop_name_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    target_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    target_name_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    ingredient_or_formulation_name: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True
    )
    pesticide_name_eng: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    corporation_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    product_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    usage_purpose_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    formulation_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_timing: Mapped[str | None] = mapped_column(Text, nullable=True)
    dilution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    dilution_factor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_quantity: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_use_count_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_use_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human_livestock_toxicity: Mapped[str | None] = mapped_column(Text, nullable=True)
    ecotoxicity: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_registered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    registration_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    render_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)

    details: Mapped[PesticideApplication] = relationship(back_populates="document_entry")
