import { Link, useLocation } from 'react-router-dom';
import { BANK_TRANSFER_PAYMENT_LABEL } from '@/lib/orderPromotion';
import { formatPrice } from '@/lib/utils';

type OrderCompleteState = {
  orderId?: number;
  totalPrice?: number;
  paymentMethod?: string;
};

export default function OrderCompletePage() {
  const { state } = useLocation();
  const orderState = (state ?? {}) as OrderCompleteState;
  const paymentMethod = orderState.paymentMethod ?? BANK_TRANSFER_PAYMENT_LABEL;
  const totalPrice = orderState.totalPrice ?? 0;

  return (
    <div className="max-w-lg mx-auto px-4 py-16">
      <div className="rounded-lg border bg-white p-8 text-center shadow-sm">
        <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-[#03C75A] text-3xl font-bold text-white">&#10003;</div>
        <h1 className="text-2xl font-bold mb-2">주문이 정상적으로 제출되었습니다</h1>
        <p className="text-gray-500">무통장입금 확인이 완료된 주문으로 접수되었습니다.</p>

        <div className="my-8 rounded-lg bg-green-50 p-4 text-left text-sm">
          {orderState.orderId != null && (
            <div className="mb-2 flex justify-between gap-4">
              <span className="text-gray-600">주문번호</span>
              <span className="font-bold">#{orderState.orderId}</span>
            </div>
          )}
          <div className="mb-2 flex justify-between gap-4">
            <span className="text-gray-600">결제 수단</span>
            <span className="font-bold text-[#03C75A]">{paymentMethod}</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-gray-600">최종 결제금액</span>
            <span className="font-bold text-[#03C75A]">{formatPrice(totalPrice)}</span>
          </div>
        </div>

      <div className="flex gap-3 justify-center">
        <Link to="/mypage/orders" className="px-6 py-3 border-2 border-[#03C75A] text-[#03C75A] rounded-lg font-bold hover:bg-green-50">
          주문내역 보기
        </Link>
        <Link to="/" className="px-6 py-3 bg-[#03C75A] text-white rounded-lg font-bold hover:bg-green-600">
          쇼핑 계속하기
        </Link>
      </div>
      </div>
    </div>
  );
}
