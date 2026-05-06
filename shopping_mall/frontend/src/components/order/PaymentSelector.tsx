import { BANK_TRANSFER_PAYMENT_LABEL } from '@/lib/orderPromotion';

const methods = ['신용카드', BANK_TRANSFER_PAYMENT_LABEL, '카카오페이', '네이버페이'];

interface Props {
  selected: string;
  onChange: (method: string) => void;
}

export default function PaymentSelector({ selected, onChange }: Props) {
  return (
    <div className="bg-white rounded-lg border p-6">
      <h3 className="font-bold text-lg mb-4">결제 수단</h3>
      <div className="space-y-2">
        {methods.map((m) => {
          const enabled = m === BANK_TRANSFER_PAYMENT_LABEL;
          return (
            <label key={m} className={`flex items-center gap-2 ${enabled ? 'cursor-pointer' : 'cursor-not-allowed text-gray-400'}`}>
              <input
                type="radio"
                name="payment"
                checked={selected === m}
                onChange={() => enabled && onChange(m)}
                disabled={!enabled}
                className="accent-[#03C75A] disabled:accent-gray-300"
              />
              <span className="text-sm">{m}</span>
              {!enabled && <span className="text-xs text-gray-400">준비 중</span>}
            </label>
          );
        })}
      </div>
    </div>
  );
}
