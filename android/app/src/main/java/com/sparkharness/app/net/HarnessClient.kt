package com.sparkharness.app.net

import com.google.gson.annotations.SerializedName
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

/** Spark Harness 服务端 REST 模型与客户端（Retrofit + 协程）。 */

data class HealthResponse(
    val ok: Boolean = false,
    val provider: String? = null,
    val tools: Int = 0,
    val skills: Int = 0,
    val plugins: Int = 0,
)

data class SkillDto(
    val name: String = "",
    val title: String = "",
    val description: String = "",
    val tools: List<String> = emptyList(),
)

data class ChatRequest(val message: String)

data class ChatResponse(
    val reply: String? = null,
    val error: String? = null,
)

interface HarnessApi {
    @GET("/api/health")
    suspend fun health(): Response<HealthResponse>

    @GET("/api/skills")
    suspend fun skills(): Response<List<SkillDto>>

    @POST("/api/chat")
    suspend fun chat(@Body body: ChatRequest): Response<ChatResponse>
}

object HarnessClient {
    /** 根据服务端地址与令牌构造 Retrofit 实例（每次地址变化重新构造）。 */
    fun create(baseUrl: String, token: String): HarnessApi {
        val client = okhttp3.OkHttpClient.Builder()
            .addInterceptor { chain ->
                val req = chain.request().newBuilder()
                if (token.isNotBlank()) {
                    req.addHeader("Authorization", "Bearer $token")
                }
                // 让服务端 WebView/接口识别来自安卓客户端
                req.addHeader("X-Spark-Client", "android")
                chain.proceed(req.build())
            }
            .build()

        val retrofit = retrofit2.Retrofit.Builder()
            .baseUrl(normalize(baseUrl))
            .client(client)
            .addConverterFactory(
                retrofit2.converter.gson.GsonConverterFactory.create()
            )
            .build()
        return retrofit.create(HarnessApi::class.java)
    }

    private fun normalize(url: String): String {
        val u = url.trim().trimEnd('/')
        return if (u.endsWith("/")) u else "$u/"
    }
}
