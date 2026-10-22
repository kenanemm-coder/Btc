# Ön kayıt (veri ve sonuçlar görülmeden önce)

Bu dosya ve `spec.py`, herhangi bir yeni strateji sonucu görülmeden önce commit edilmiştir.
Kurallar sonradan değiştirilirse bu, raporda ayrıca ve açıkça belirtilir.

- **Veri:** Binance Spot BTC/TRY, yerel 1h ve 4h mumlar (aday B/C/D), 5 dk mumlar (A referansı). Isınma 2020-10-01, değerlendirme 2021-01-01 →.
- **Bölme:** Değerlendirme süresinin ilk %60'ı eğitim, sonraki %20 doğrulama, son %20 test. Seçim aşaması test verisini **indirmez**.
- **Dolum:** Sinyal kapanmış mumda, dolum bir sonraki mum açılışında. Stop muhafazakâr (mum düşüğü stopa değdiyse tetiklenmiş; açılış boşluğunda açılış fiyatı).
- **Maliyet:** temel %0,10 komisyon + %0,05 slipaj (her yön); yüksek %0,15 + %0,10; çok yüksek %0,20 + %0,20; gecikmeli = temel + 1 mum ek gecikme.
- **Aday sayısı:** 26 ayar (A:1, B:16, C:4, D:4, E:1). Ek olarak A stratejisi önceki dayanıklılık analizinde 27 komşu ayarla görüldü. Çoklu denemede en iyinin tesadüfen iyi çıkma olasılığı raporda belirtilir.
- **Seçim:** aile başına tek ayar; skor = min(Sharpe_eğitim, Sharpe_doğrulama); en az 20 işlem.
- **Test:** seçim dondurulduktan sonra bir kez. Kabul kapıları G1–G8 `spec.py`'de.
- **Bilinen bulaşma:** Önceki rapor 2022–2026 BTC/TRY fiyat yönünü (TRY'de güçlü yükseliş) ve A stratejisinin sonucunu gösterdi; bu bilgi araştırmacıda zaten var. Bu yüzden trend takibi adaylarına yapısal avantaj olabilir; al-tuta göre karşılaştırma ve kapı G8 bunun içindir.
- **Sınırlar:** Enflasyon/kur düzeltmesi yapılmaz (gerçek veri yok). Sharpe'ta risksiz oran 0 alınır (TRY faizi yüksek olduğundan Sharpe iyimser). Gerçek slipaj ve emir defteri modellenmez.
