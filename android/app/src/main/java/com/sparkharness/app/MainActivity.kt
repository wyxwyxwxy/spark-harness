package com.sparkharness.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.ProgressBar
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.sparkharness.app.util.Prefs

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var progress: ProgressBar

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        progress = findViewById(R.id.progress)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.userAgentString = "SparkHarness/1.0 (Android)"
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: android.webkit.WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                progress.visibility = ProgressBar.VISIBLE
            }
            override fun onPageFinished(view: android.webkit.WebView?, url: String?) {
                progress.visibility = ProgressBar.GONE
            }
        }

        findViewById<Button>(R.id.btnConnect).setOnClickListener { connect() }
        findViewById<Button>(R.id.btnRefresh).setOnClickListener { webView.reload() }
        findViewById<Button>(R.id.btnSettings).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // 通知权限（Android 13+），用于前台连接服务的常驻通知
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                1001
            )
        }

        if (Prefs.serverUrl.isBlank()) {
            Toast.makeText(this, R.string.toast_save_first, Toast.LENGTH_LONG).show()
            startActivity(Intent(this, SettingsActivity::class.java))
        } else {
            connect()
            if (Prefs.keepService) {
                ConnectionService.start(this)
            }
        }
    }

    private fun connect() {
        val url = Prefs.serverUrl.trim()
        if (url.isBlank()) {
            Toast.makeText(this, R.string.toast_save_first, Toast.LENGTH_SHORT).show()
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        // 若设置了令牌，通过 Cookie 传递给 WebView（服务端可据此鉴权）
        if (Prefs.token.isNotBlank()) {
            CookieManager.getInstance().apply {
                setAcceptCookie(true)
                setCookie(url, "spark_token=${Prefs.token}")
            }
        }
        webView.loadUrl(url.trimEnd('/') + "/")
        Toast.makeText(this, R.string.toast_connected, Toast.LENGTH_SHORT).show()
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }
}
