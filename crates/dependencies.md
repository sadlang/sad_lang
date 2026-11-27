# اتّجاهُ التبعيّة بين الحزم

تفرض القاعدة ٢٥ من دليل الأسلوب أن يُكتب اتّجاهُ التبعيّة بين حزم المترجم، وأن يفحصه السكربت.

- **مصدرُ الحقيقة** هو [`tools/checks/crate_deps.toml`](../tools/checks/crate_deps.toml)، وهذه الوثيقةُ تشرحه.
- **الجدولُ في آخرها مولَّدٌ** من ذلك الملفّ، فلا يُعدَّل باليد (القاعدة ٢٢).
- **ولا يغيّر الملفَّ إلّا المالك،** في طلب دمجٍ منفصل (القاعدة ٢٠).

## القواعد

يرفض السكربتُ (`python tools/checks/check.py crate-deps --check`) ما يخالف أيًّا منها:

1. **كلُّ حزمةٍ لها قائمةٌ بما تعتمد عليه، ولا تعتمد على غيرها.** ويشمل ذلك `dependencies` و`dev-dependencies` و`build-dependencies`.
2. **التبعيّةُ إلى طبقةٍ أدنى وحدها.** فلا دورانَ بين الحزم، ولا تبعيّةَ بين حزمتين في طبقةٍ واحدة.
3. **لا يبلغ `sad_llvm` إلّا أربع حزم:** `sad_codegen_llvm` و`sad_link` و`sad_driver` و`sad_worker`. ولا تبلغه غيرُها، لا مباشرةً ولا بواسطة حزمةٍ أخرى.
4. **كلُّ حزمةٍ في `crates/` مذكورةٌ في الملفّ،** ولا يُذكر فيه ما ليس فيها.

## العلّة

- **`unsafe` وLLVM في حزمةٍ واحدة** (ق‑٥، القاعدة ٣): `sad_llvm` هي الحدّ الضيّق مع LLVM. والحزمُ فوقها آمنة، ولا تكشف أنواعَ LLVM في واجهتها (القاعدة ٣١).
- **حالةُ LLVM العامّة في البرنامج الغلاف** (القاعدة ٤): لذلك يعتمد `sad_driver` و`sad_worker` على `sad_llvm` مباشرةً، لتهيئة LLVM.
- **خادمُ اللغة بلا LLVM ولا مترجم** (ق‑٩):
  - الترجمةُ في عمليّةٍ عاملةٍ منفصلة. فـ`sad_lsp` لا يعتمد على `sad_session`.
  - ويكلّم العاملَ عبر `sad_worker_protocol`، ويعرض التشخيصَ بـ`sad_diagnostics`.
  - فإن انهار المترجمُ لم يسقط الخادم.
- **`sad_session` يقف عند التمثيل الوسيط:** ينسّق التحليلَ وحده، فلا يجرّ LLVM إلى من لا يحتاجه.
  - والتوليدُ والربطُ يستدعيهما البرنامجُ الغلاف والعاملُ بعده.
  - فالتكرارُ بينهما بضعةُ نداءات، والفصلُ ثابتٌ في الرسم نفسِه بلا خيارات بناء.
- **التشخيصُ في طبقةٍ منخفضة** (ق‑٦ و): كلُّ مرحلةٍ ترسل تشخيصَها إلى المحرّك الواحد. ويعتمد هو على `sad_bidi` ليرتّب السطرَ عند العرض في الطرفيّة (ق‑٦ ز).
- **التسميةُ الثابتة للرموز في `sad_link`** (ق‑٩ ١-٣): يحتاجها التوليدُ والرابطُ وجدولُ إعادة التحميل. فـ`sad_link` تحت `sad_codegen_llvm`.
- **`sad_check` في القاع:** يستعمل كلُّ حزمةٍ ماكرو التوكيد الدائم (القاعدة ٥٢).

## الطبقات

- **الأساس:** ماكرو التوكيد.
- **البنية التحتيّة:**
  - المخصِّصاتُ المجمَّعة.
  - والموضع.
  - وجداولُ Unicode.
  - والحدُّ مع LLVM، وليس له إلّا `sad_check`.
- **الاتّجاه والتشخيص:** ما يعرض الخطأ.
- **الواجهةُ الأماميّة:** اللفظ، ثمّ النحو، ثمّ الأسماء، ثمّ الأنواع، ثمّ الملكيّة. وكلُّ مرحلةٍ في طبقة، فلا تعتمد مرحلةٌ على ما بعدها.
- **التمثيلُ الوسيط، ثمّ الخلفيّةُ والتنسيق.**
- **البرامج:**
  - `sad_driver`، وهو غلافٌ رقيق (القاعدة ١٥).
  - والعامل.
  - وخادمُ اللغة.

## الجدول

<!-- generated:crate-deps:begin -->
| الطبقة | الحزمة | تعتمد على |
|---|---|---|
| 0 الأساس | `sad_check` | — |
| 0 الأساس | `sad_toml` | — |
| 0 الأساس | `sad_unicode` | — |
| 1 البنية التحتيّة | `sad_arena` | `sad_check` |
| 1 البنية التحتيّة | `sad_llvm` | `sad_check` |
| 1 البنية التحتيّة | `sad_span` | `sad_check`, `sad_toml`, `sad_unicode` |
| 2 الاتّجاه | `sad_bidi` | `sad_check`, `sad_unicode` |
| 3 التشخيص | `sad_diagnostics` | `sad_check`, `sad_toml`, `sad_span`, `sad_unicode`, `sad_bidi` |
| 4 اللفظ والشجرة والربط والبروتوكول | `sad_ast` | `sad_check`, `sad_arena`, `sad_span` |
| 4 اللفظ والشجرة والربط والبروتوكول | `sad_lexer` | `sad_check`, `sad_span`, `sad_unicode`, `sad_diagnostics` |
| 4 اللفظ والشجرة والربط والبروتوكول | `sad_link` | `sad_check`, `sad_unicode`, `sad_diagnostics`, `sad_llvm` |
| 4 اللفظ والشجرة والربط والبروتوكول | `sad_worker_protocol` | `sad_check`, `sad_span`, `sad_diagnostics` |
| 5 النحو | `sad_parser` | `sad_check`, `sad_arena`, `sad_span`, `sad_unicode`, `sad_diagnostics`, `sad_lexer`, `sad_ast` |
| 6 الأسماء | `sad_resolve` | `sad_check`, `sad_arena`, `sad_span`, `sad_unicode`, `sad_diagnostics`, `sad_ast` |
| 7 الأنواع | `sad_types` | `sad_check`, `sad_arena`, `sad_span`, `sad_diagnostics`, `sad_ast`, `sad_resolve` |
| 8 الملكيّة | `sad_ownership` | `sad_check`, `sad_arena`, `sad_span`, `sad_diagnostics`, `sad_ast`, `sad_resolve`, `sad_types` |
| 9 التمثيل الوسيط | `sad_ir` | `sad_check`, `sad_arena`, `sad_span`, `sad_diagnostics`, `sad_ast`, `sad_resolve`, `sad_types`, `sad_ownership` |
| 10 الخلفيّة والتنسيق | `sad_codegen_llvm` | `sad_check`, `sad_arena`, `sad_span`, `sad_diagnostics`, `sad_ir`, `sad_link`, `sad_llvm` |
| 10 الخلفيّة والتنسيق | `sad_session` | `sad_check`, `sad_arena`, `sad_span`, `sad_unicode`, `sad_diagnostics`, `sad_lexer`, `sad_ast`, `sad_parser`, `sad_resolve`, `sad_types`, `sad_ownership`, `sad_ir` |
| 11 البرامج | `sad_driver` | `sad_check`, `sad_unicode`, `sad_span`, `sad_diagnostics`, `sad_session`, `sad_codegen_llvm`, `sad_link`, `sad_llvm` |
| 11 البرامج | `sad_lsp` | `sad_check`, `sad_span`, `sad_diagnostics`, `sad_worker_protocol` |
| 11 البرامج | `sad_worker` | `sad_check`, `sad_span`, `sad_diagnostics`, `sad_session`, `sad_codegen_llvm`, `sad_link`, `sad_llvm`, `sad_worker_protocol` |
<!-- generated:crate-deps:end -->
