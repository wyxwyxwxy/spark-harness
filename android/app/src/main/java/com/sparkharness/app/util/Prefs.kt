package com.sparkharness.app.util

import android.content.Context
import android.content.SharedPreferences

/** 简单的 SharedPreferences 封装：保存服务端地址 / 令牌 / 开关。 */
object Prefs {
    private const val NAME = "spark_harness_prefs"
    private const val KEY_URL = "server_url"
    private const val KEY_TOKEN = "token"
    private const val KEY_AUTOSTART = "autostart"
    private const val KEY_SERVICE = "keep_service"

    private fun sp(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp(AppContext.get()).getString(KEY_URL, "") ?: ""
        set(v) = sp(AppContext.get()).edit().putString(KEY_URL, v).apply()

    var token: String
        get() = sp(AppContext.get()).getString(KEY_TOKEN, "") ?: ""
        set(v) = sp(AppContext.get()).edit().putString(KEY_TOKEN, v).apply()

    var autostart: Boolean
        get() = sp(AppContext.get()).getBoolean(KEY_AUTOSTART, false)
        set(v) = sp(AppContext.get()).edit().putBoolean(KEY_AUTOSTART, v).apply()

    var keepService: Boolean
        get() = sp(AppContext.get()).getBoolean(KEY_SERVICE, false)
        set(v) = sp(AppContext.get()).edit().putBoolean(KEY_SERVICE, v).apply()
}
