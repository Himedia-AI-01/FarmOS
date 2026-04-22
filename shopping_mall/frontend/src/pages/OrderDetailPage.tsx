import { Link, useParams } from 'react-router-dom';
import { useOrder } from '@/hooks/useOrders';
import { formatPrice, formatDate } from '@/lib/utils';
import type { ShippingAddress } from '@/types/order';

/** 백엔드가 JSON 문자열로 저장할 수 있는 필드를 안전하게 파싱합니다. */
function parseJsonField<T>(value: unknown): T | null {
  if (!value) return null;
  if (typeof value === 'object') return value as T;
  if (typeof value === 'string') {
    try { return JSON.parse(value) as T; } catch { return null; }
  }
  return null;
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending:   { label: '주문접수',   color: 'bg-yellow-100 text-yellow-800' },
  paid:      { label: '결제완료',   color: 'bg-blue-100 text-blue-800' },
  shipping:  { label: '배송중',     color: 'bg-purple-100 text-purple-800' },
  delivered: { label: '배송완료',   color: 'bg-green-100 text-green-800' },
  cancelled: { label: '취소',       color: 'bg-red-100 text-red-800' },
};

export default function OrderDetailPage() {
  const { orderId } = useParams<{ orderId: string }>();
  // /^\d+$/ rejects empty, negatives, floats, and non-numeric strings
  const isValidId = /^\d+$/.test(orderId ?? '') && Number(orderId) > 0;
  const numericId = isValidId ? Number(orderId) : 0;
  // pass 0 when invalid so useOrder's `enabled: !!id` suppresses the fetch
  const { data: order, isLoading, isError } = useOrder(numericId);

  if (!isValidId) {
    return (
      <div className="text-center py-20">
        <p className="text-gray-500">잘못된 주문 번호입니다.</p>
        <Link to="/mypage/orders" className="text-[#03C75A] text-sm mt-2 inline-block">
          ← 주문내역으로 돌아가기
        </Link>
      </div>
    );
  }

  if (isLoading) {
    return <div className="text-center py-20 text-gray-400">로딩 중...</div>;
  }

  if (isError || !order) {
    return (
      <div className="text-center py-20">
        <p className="text-gray-500">주문 정보를 불러올 수 없습니다.</p>
        <Link to="/mypage/orders" className="text-[#03C75A] text-sm mt-2 inline-block">
          ← 주문내역으로 돌아가기
        </Link>
      </div>
    );
  }

  const status = STATUS_MAP[order.status] ?? { label: order.status, color: 'bg-gray-100 text-gray-800' };
  const shippingAddress = parseJsonField<ShippingAddress>(order.shippingAddress);

  return (
    <div className="max-w-2xl mx-auto px-4 py-6">
      {/* 헤더 */}
      <div className="flex items-center gap-3 mb-6">
        <Link to="/mypage/orders" className="text-sm text-gray-400 hover:text-gray-600 transition-colors">
          ← 주문내역
        </Link>
        <h1 className="text-xl font-bold">주문 상세</h1>
      </div>

      {/* 주문 번호 / 상태 */}
      <div className="bg-white rounded-lg border p-4 mb-4">
        <div className="flex justify-between items-start">
          <div>
            <p className="font-semibold text-gray-800">주문번호 #{order.id}</p>
            <p className="text-sm text-gray-400 mt-0.5">{formatDate(order.createdAt)}</p>
          </div>
          <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${status.color}`}>
            {status.label}
          </span>
        </div>
      </div>

      {/* 주문 상품 */}
      <div className="bg-white rounded-lg border p-4 mb-4">
        <h2 className="font-semibold mb-3">주문 상품</h2>
        <div className="space-y-3">
          {order.items.map((item) => (
            <div key={item.id} className="flex items-center gap-3">
              <img
                src={item.product?.thumbnail || `https://picsum.photos/seed/p${item.productId}/64/64`}
                alt={item.product?.name || ''}
                className="w-14 h-14 rounded-lg object-cover border border-gray-100 shrink-0"
              />
              <div className="flex-1 min-w-0">
                <p className="font-medium text-sm leading-snug truncate">
                  {item.product?.name || `상품 #${item.productId}`}
                </p>
                {(() => {
                  const opt = parseJsonField<Record<string, string>>(item.selectedOption);
                  return opt && Object.keys(opt).length > 0 ? (
                    <p className="text-xs text-gray-400 mt-0.5">
                      {Object.entries(opt).map(([k, v]) => `${k}: ${v}`).join(' / ')}
                    </p>
                  ) : null;
                })()}
                <p className="text-xs text-gray-500 mt-0.5">{item.quantity}개</p>
              </div>
              <p className="font-semibold text-sm shrink-0">
                {formatPrice(item.price * item.quantity)}
              </p>
            </div>
          ))}
        </div>
        <div className="border-t mt-4 pt-3 flex justify-between items-center">
          <span className="font-semibold text-gray-700">총 결제금액</span>
          <span className="font-bold text-lg text-[#03C75A]">{formatPrice(order.totalPrice)}</span>
        </div>
      </div>

      {/* 배송 정보 */}
      {shippingAddress && (
        <div className="bg-white rounded-lg border p-4 mb-4">
          <h2 className="font-semibold mb-3">배송 정보</h2>
          <dl className="space-y-1.5 text-sm">
            {[
              { label: '수령인', value: shippingAddress.recipient },
              { label: '연락처', value: shippingAddress.phone },
              {
                label: '주소',
                value: `[${shippingAddress.zipCode}] ${shippingAddress.address}${shippingAddress.detail ? ' ' + shippingAddress.detail : ''}`,
              },
            ].map(({ label, value }) => (
              <div key={label} className="flex gap-3">
                <dt className="text-gray-400 w-14 shrink-0">{label}</dt>
                <dd className="text-gray-700">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {/* 결제 수단 */}
      {order.paymentMethod && (
        <div className="bg-white rounded-lg border p-4">
          <h2 className="font-semibold mb-2">결제 수단</h2>
          <p className="text-sm text-gray-600">{order.paymentMethod}</p>
        </div>
      )}
    </div>
  );
}
