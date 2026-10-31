# Spark Harness Android 客户端：保留 Retrofit/Gson 模型与协程
-keepattributes *Annotation*, Signature
-keep class com.sparkharness.app.net.** { *; }
-keep class kotlin.coroutines.** { *; }
-dontwarn okhttp3.**
-dontwarn retrofit2.**
