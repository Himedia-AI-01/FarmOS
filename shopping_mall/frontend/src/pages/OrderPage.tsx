import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCart } from '@/hooks/useCart';
import { useCreateOrder } from '@/hooks/useOrders';
import { formatPrice } from '@/lib/utils';
import { productFallbackImage } from '@/lib/fallbackImages';
import {
  BANK_TRANSFER_PAYMENT_LABEL,
  buildOrderCompleteState,
  getOrderItemTotal,
  getOrderTotal,
} from '@/lib/orderPromotion';
import OrderForm from '@/components/order/OrderForm';
import PaymentSelector from '@/components/order/PaymentSelector';
import type { ShippingAddress } from '@/types/order';

export default function OrderPage() {
  const navigate = useNavigate();
  const { data: cart } = useCart();
  const createOrder = useCreateOrder();
  const [address, setAddress] = useState<ShippingAddress>({ zipCode: '', address: '', detail: '', recipient: '', phone: '' });
  const [paymentMethod, setPaymentMethod] = useState(BANK_TRANSFER_PAYMENT_LABEL);

  const items = cart?.items ?? [];
  const totalPrice = getOrderTotal(items);

  const handleOrder = () => {
    createOrder.mutate(
      {
        items: items.map((i) => ({ productId: i.productId, quantity: i.quantity, selectedOption: i.selectedOption ?? undefined })),
        shippingAddress: address,
        paymentMethod,
      },
      { onSuccess: (order) => navigate('/order/complete', { state: buildOrderCompleteState(order) }) }
    );
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <h1 className="text-xl font-bold mb-6">주문서 작성</h1>
      <div className="space-y-6">
        <OrderForm address={address} onChange={setAddress} />
        <PaymentSelector selected={paymentMethod} onChange={setPaymentMethod} />
        <div className="bg-white rounded-lg border p-6">
          <h3 className="font-bold text-lg mb-4">주문 상품 ({items.length})</h3>
          {items.map((i) => (
            <div key={i.id} className="flex items-center gap-3 py-2 border-b last:border-0">
              <img src={i.product.thumbnail || productFallbackImage(i.productId, 60)} alt="" className="w-12 h-12 rounded" />
              <div className="flex-1 text-sm">
                <p>{i.product.name}</p>
                <p className="text-gray-500">수량: {i.quantity}</p>
              </div>
              <div className="text-right text-sm">
                <p className="font-bold text-[#03C75A]">{formatPrice(getOrderItemTotal(i))}</p>
              </div>
            </div>
          ))}
          <div className="flex justify-between items-center mt-4 pt-4 border-t font-bold text-lg">
            <span>총 결제금액</span>
            <span className="text-[#03C75A]">{formatPrice(totalPrice)}</span>
          </div>
          <p className="mt-2 text-sm text-[#03C75A]">무통장입금으로 주문 접수와 동시에 입금 확인 완료 처리됩니다.</p>
        </div>
        <button onClick={handleOrder} disabled={createOrder.isPending} className="w-full py-4 bg-[#03C75A] text-white rounded-lg font-bold text-lg hover:bg-green-600 disabled:bg-gray-300">
          {createOrder.isPending ? '주문 처리 중...' : '주문하기'}
        </button>
      </div>
    </div>
  );
}
