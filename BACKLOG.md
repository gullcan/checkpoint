# Checkpoint — eleştiri ve gelecek sürümler

Bu dosya uygulanmış özellik listesi değildir. Öncelikler kişisel kullanımda görülen sorunlara göre değişebilir. Yeni teknoloji eklemek başarı ölçütü değildir.

## Bugünkü hali gerçekten işe yarar mı?

**Başlamayı kolaylaştırma ve kaldığın yeri hatırlatma açısından makul bir tasarım; etkili olduğu henüz gösterilmedi.** Görev seçimini daraltıyor, büyük işi kısa bir başlangıca çeviriyor ve sonraki kullanımda geçmişi koruyor. Bu mekanizmaların kodda bulunması, kişinin daha çok veya daha iyi çıktı ürettiğini kanıtlamaz.

| Eleştiri | Kullanıcıya etkisi | En küçük çözüm fikri |
| --- | --- | --- |
| Bir işe başlamadan yedi alanla görev tanımlamak gerekiyor | Sistemi doldurmak çalışmanın önüne geçebilir | Önce hangi alanlarda takılındığını gözle; sonra adım adım görev girişi |
| Komutlar hâlâ ezber ve yazma gerektiriyor | Telefonda giriş zahmeti | En sık kullanılan seçimlere birkaç düğme; doğal dil ajanı eklemek gerekmiyor |
| Her plan iki iş önerebilir, ama sürekli yeniden planlamak mümkün | Odaklanmak yerine seçim döngüsüne girilebilir | Mevcut adımı öne çıkarıp yeni planın neden istendiğini basitçe sormak |
| Aynı yüksek puanlı görev tekrar tekrar öne çıkabilir | Daha az acil ama önemli işler bekler | Önce kaç kez ertelendiğini gözle; yaşa göre puan eklemeyi hemen yapma |
| Her işe 15 dakika ayırmak bazı işlere uymaz | Hazırlık uzun sürer veya küçük ama anlamsız işler seçilir | Kullanıcının kısa/normal çalışma tercihi; yalnızca gerçek ihtiyaç oluşursa |
| Hedef/bağlam kendiliğinden güncellenmez; devam notu modele gönderilmez | Tekrarlanan veya güncelliğini kaybetmiş öneriler | Son notu kullanıcıya gösterip bağlamı güncellemesini istemek |
| Zaman/enerji değişince kayıtlı adım aynen kalır | 15 dakikalık adım 5 dakikalık zamana uymayabilir | “Bu adım hâlâ uygun mu?” kontrolü; onaysız otomatik yeniden yazma yok |
| Çıktı sayısı kolayca başarı göstergesi sanılabilir | Çok kayıt tutmak anlamlı iş üretmekle karışır | Ana hedefe katkıyı haftalık olarak insanın değerlendirmesi |
| Bot yalnızca bilgisayar ve süreç açıkken çalışır | İhtiyaç anında erişilememe | Önce gerçekten kullanım kaybı yarattığını gözle; sonra barındırmayı değerlendir |

Destekleyici dil, açık açıklama ve seçim hakkı kullanıcı deneyimini iyileştirmeyi amaçlar. Bot kendisini nörobilimci/terapist olarak tanıtmaz; kullanıcı hakkında psikolojik çıkarım veya etkinlik garantisi üretmez. Teknik güven; açık kurallardan, dürüst sınırlardan ve düzgün hata davranışından gelir.

## v1.1 — Gerçek görevlerle kişisel pilot

**Problem:** Teknik denemeler gerçek kullanım faydasını göstermiyor.

**Why now:** Core loop ve API gerektirmeyen testler var; artık yeni özellikten önce kullanım gözlemi daha değerli.

**En küçük çözüm:** 7–14 günlük kişisel kullanım. Bu süre bilimsel yeterlilik eşiği değil, küçük bir ürün değerlendirme denemesidir. Bir seferde az sayıda gerçek görevle başla. Her çalışmada mevcut çıktı/engel/süre alanlarını kullan. Dışarıda kısa bir günlükte şu soruları yanıtla:

- Başlamak daha kolay mıydı, yoksa botla uğraşmak işi geciktirdi mi?
- Öneriyi kullandım mı, değiştirdim mi? Neden?
- Ortaya çıkan adım ana hedefime katkı sağladı mı?
- Kullanmadığım günlerde neden kullanmadım?
- Kayıt tutma yükü faydasından büyük müydü?

**Değerlendirme:** Checkpoint toplamını tek başarı ölçütü yapma. Haftanın sonunda yararlı ve yararsız örnekleri birlikte incele. Dilersen kendi yapabilme algına ilişkin kısa kişisel not al; bu klinik veya doğrulanmış bir ölçek değildir. Önce/sonra farkı tek başına nedensellik göstermez; görev zorluğu, zaman ve motivasyon da değişir.

**Kabul ölçütü:** Hangi kullanım sorununun en sık tekrarlandığını somut örneklerle belirleyebilmek. Fayda görünmüyorsa yeni özellik eklemek yerine akışı daha da azaltmak veya kullanmayı bırakmak geçerli sonuçtur.

**Dependency:** Yok. Telemetri, veri toplama servisi veya dashboard gerekmiyor.

## v1.2 — Daha az giriş yükü

**Tetikleyici:** Pilot sırasında görev ekleme veya komut yazma belirgin şekilde kullanımı engelliyorsa.

- Görev adını, hedefi ve kalan alanları sırayla soran basit Telegram akışı.
- İptal/geri dön davranışı ve yarım kalan girişin açık gösterimi.
- Sık kullanılan seçimlerde az sayıda düğme; bütün komut sistemini değiştirme zorunluluğu yok.
- Süre/önem/yük için varsayılanlar ancak kullanıcıya gösterilip düzeltilebiliyorsa.

**Trade-off:** Daha az yazma karşılığında daha fazla sohbet durumu ve test gerekecek.

**Kabul ölçütü:** Kullanıcının yardım metnini yeniden okumadan bir görev ekleyip çalışma seçebilmesi; yarım bırakılan girişin görev oluşturmaması.

**Dependency:** Mevcut Python ve Telegram API ile başlanabilir. Framework gerekmiyor.

## v1.3 — Daha iyi devam etme ve eylem kalitesi

**Tetikleyici:** Tekrarlanan, gereğinden büyük veya güncel bağlamı kaçıran öneriler görülüyorsa.

- Son ilerleme/engel notunu göstererek bağlamı güncelleme; kullanıcının yazısını otomatik olarak gerçek kabul edip üzerine yeni bilgi uydurmama.
- Süre veya enerji önemli ölçüde değiştiğinde kayıtlı adımı yeniden değerlendirme seçeneği.
- LLM geçmişine ve çıktısına sınır; eski bağlamın kaybını açıkça değerlendirme.
- Az sayıda anonimleştirilmiş örnekte insan incelemesi: hedefe uygun mu, yapılabilir mi, gözlenebilir sonucu var mı, uydurulmuş ayrıntı içeriyor mu?
- Ancak ihtiyaç kanıtlanırsa 15 dakika sınırını kullanıcı seçimine açma.

**Trade-off:** Daha fazla soru ve bağlam, işe başlama hızını azaltabilir. Daha çok AI çağrısı daha iyi öneri garantisi vermez.

**Kabul ölçütü:** Tekrarlanan gerçek hata örneklerinin yeni davranışla azalıp azalmadığını görmek; küçük örneklemde evrensel kalite oranı iddia etmemek.

**Dependency:** Önce mevcut SDK ve standart kütüphane.

## Daha sonra — yalnızca somut ihtiyaç varsa

- **CI:** Aynı unittest komutunu GitHub üzerinde çalıştırmak; yerelde geçen testlerle karıştırmadan durumunu göstermek.
- **Kurtarma:** Kullanıcı ihtiyacı varsa açık yedekleme/geri yükleme komutları. Atomik kayıt zaten yedekleme değildir.
- **Eşzamanlı kullanım:** CLI ve botu birlikte çalıştırma ihtiyacı oluşursa önce tek-yazıcı kilidi veya SQLite değerlendirmesi. PostgreSQL ilk seçenek değil.
- **Sürekli erişim:** Yerel çalışma gerçekten kullanım engeliyse barındırma maliyeti, kişisel verinin konumu ve bakım yükünü birlikte değerlendirmek.
- **LLM çağrısı sırasında yanıt verme:** Mevcut bot senkron çalışır; 20 saniyeye kadar model beklemesi başka komutları geciktirebilir. Ancak hissedilir bir sorun olursa ele alınmalı.

## DO NOT ADD YET

LangChain/LangGraph, vector database, Redis, Docker, microservices, message queue, multi-agent, çok kullanıcılı authentication, rozet/streak/baskı üreten oyunlaştırma, kişiye psikolojik profil çıkarma.

Her öneri için kapı: **Somut problem → şu an mevcut olduğuna dair örnek → mevcut stack ile en küçük çözüm → ancak gerekiyorsa yeni dependency.**
