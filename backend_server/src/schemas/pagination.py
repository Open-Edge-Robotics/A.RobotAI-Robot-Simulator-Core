from typing import ClassVar, List, Optional, TypeVar, Generic
from pydantic import BaseModel, Field, field_validator, validator

T = TypeVar('T')

class PaginationParams(BaseModel):
    """페이지네이션 요청 파라미터"""
    page: int = Field(default=1, ge=1, description="페이지 번호 (1부터 시작)")
    size: Optional[int] = Field(None, description="페이지 크기 (1 이상, 선택)")
    
    DEFAULT_SIZE: ClassVar[int] = 10  # size가 없을 때 기본값

    @property
    def offset(self) -> int:
        """SQLAlchemy OFFSET 값 계산"""
        effective_size = self.size if self.size is not None else self.DEFAULT_SIZE
        return (self.page - 1) * effective_size

    @property
    def limit(self) -> int:
        """SQLAlchemy LIMIT 값"""
        return self.size if self.size is not None else self.DEFAULT_SIZE
    
    @field_validator("page")
    @classmethod
    def validate_page(cls, v):
        if v is not None and v < 1:
            raise ValueError("페이지 번호는 1 이상이어야 합니다.")
        return v
    
class PaginationMeta(BaseModel):
    """페이지네이션 메타데이터"""
    current_page: int = Field(description="현재 페이지 번호", alias="currentPage")
    page_size: int = Field(description="페이지당 항목 수", alias="pageSize")
    total_items: int = Field(description="총 항목 수", alias="totalItems")
    total_pages: int = Field(description="총 페이지 수", alias="totalPages")
    has_next: bool = Field(description="다음 페이지 존재 여부", alias="hasNext")
    has_previous: bool = Field(description="이전 페이지 존재 여부", alias="hasPrevious")
    
    @classmethod
    def create(cls, page: int, size: int, total_items: int) -> 'PaginationMeta':
        """페이지네이션 메타데이터 생성"""
        total_pages = (total_items + size - 1) // size  # 올림 계산
        has_next = page < total_pages
        has_previous = page > 1
        
        return cls(
            current_page=page,
            page_size=size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=has_next,
            has_previous=has_previous
        )
        
    class Config:
        populate_by_name = True