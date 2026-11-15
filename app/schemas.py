"""Pydantic schemas for request/response validation and serialization."""

from datetime import datetime
from typing import List, Optional, Union

from pydantic import BaseModel, EmailStr, Field, ConfigDict


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- Auth ----

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    role: str

    model_config = ConfigDict(from_attributes=True)


class SessionInfo(BaseModel):
    session_id: str
    csrf_token: str


# ---- Categories ----

class CategoryOut(BaseModel):
    id: int
    name: str
    slug: str

    model_config = ConfigDict(from_attributes=True)


# ---- Products / Variants ----

class ProductVariantOut(BaseModel):
    id: int
    sku: str
    options: Optional[dict]
    price_minor: int
    currency: str
    stock: int
    reserved_stock: int
    active: bool

    model_config = ConfigDict(from_attributes=True)


class ProductOut(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str]
    delivery_type: str
    active: bool
    price_minor: int
    currency: str
    variants: List[ProductVariantOut] = []
    images: List[str] = []
    category: Optional[CategoryOut] = None

    model_config = ConfigDict(from_attributes=True)


class ProductList(BaseModel):
    items: List[ProductOut]
    page: int
    total: int


# ---- Admin product input ----

class ProductVariantCreate(BaseModel):
    sku: str = Field(min_length=1)
    options: Optional[dict] = None
    price_minor: int = Field(ge=0)
    currency: str = "USD"
    stock: int = Field(ge=0, default=0)


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    delivery_type: str = "ship"
    category_id: Optional[int] = None
    variants: List[ProductVariantCreate] = []


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    delivery_type: Optional[str] = None
    active: Optional[bool] = None
    category_id: Optional[int] = None


# ---- Errors ----

class ErrorOut(BaseModel):
    code: str
    message: str


# ---- Cart ----

class CartItemIn(BaseModel):
    variant_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=100)


class CartItemOut(BaseModel):
    variant_id: int
    sku: str
    product_name: str
    quantity: int
    price_minor: int
    currency: str
    delivery_type: str
    line_total_minor: int

    model_config = ConfigDict(from_attributes=True)


class CartOut(BaseModel):
    items: List[CartItemOut] = []
    subtotal_minor: int
    shipping_minor: int
    total_minor: int
    currency: str
    item_count: int


# ---- Checkout / Orders ----

class OrderCreate(BaseModel):
    shipping_address: Optional[Union[str, dict]] = None


class OrderItemOut(BaseModel):
    id: int
    variant_id: int
    product_name: str
    quantity: int
    price_minor: int
    delivery_type: str

    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: int
    status: str
    subtotal_minor: int
    shipping_minor: int
    total_minor: int
    currency: str
    email: str
    razorpay_order_id: Optional[str] = None
    payment_status: Optional[str] = None
    created_at: datetime
    items: List[OrderItemOut] = []
    downloadable_item_ids: List[int] = []

    model_config = ConfigDict(from_attributes=True)


class OrderListOut(BaseModel):
    items: List[OrderOut]
    total: int


class PaymentVerify(BaseModel):
    razorpay_payment_id: str = Field(min_length=1)
    razorpay_order_id: str = Field(min_length=1)
    razorpay_signature: str = Field(min_length=1)


class CheckoutResponse(BaseModel):
    order_id: int
    razorpay_order_id: str
    key_id: str
    amount_minor: int
    currency: str


# ---- Download grant ----

class DownloadTokenOut(BaseModel):
    token: str
    expires_at: datetime


# ---- Image upload ----

class ImageUploadOut(BaseModel):
    storage_key: str
    url: str
    content_type: str
    byte_size: int
    width: int
    height: int


# ---- Digital asset (admin) ----

class DigitalAssetOut(BaseModel):
    id: int
    order_item_id: int
    file_key: str
    content_type: str
    byte_size: int

    model_config = ConfigDict(from_attributes=True)


class DigitalAssetAttachOut(BaseModel):
    id: int
    storage_key: str
    content_type: str
    byte_size: int
    product_id: int


# ---- Wishlist ----

class WishlistItemIn(BaseModel):
    product_id: int = Field(ge=1)


class WishlistItemOut(BaseModel):
    id: int
    product_id: int

    model_config = ConfigDict(from_attributes=True)


# ---- Reviews ----

class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    body: Optional[str] = Field(default=None, max_length=5000)


class ReviewUpdate(BaseModel):
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    body: Optional[str] = Field(default=None, max_length=5000)


class ReviewOut(BaseModel):
    id: int
    user_id: int
    product_id: int
    rating: int
    body: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
