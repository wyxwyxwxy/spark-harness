package com.sparkharness.app

import android.os.Bundle
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.sparkharness.app.net.HarnessClient
import com.sparkharness.app.util.Prefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class SettingsActivity : AppCompatActivity() {

    private lateinit var editUrl: EditText
    private lateinit var editToken: EditText
    private lateinit var chkAutostart: CheckBox
    private lateinit var chkService: CheckBox

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        editUrl = findViewById(R.id.editUrl)
        editToken = findViewById(R.id.editToken)
        chkAutostart = findViewById(R.id.chkAutostart)
        chkService = findViewById(R.id.chkService)

        editUrl.setText(Prefs.serverUrl)
        editToken.setText(Prefs.token)
        chkAutostart.isChecked = Prefs.autostart
        chkService.isChecked = Prefs.keepService

        findViewById<Button>(R.id.btnSave).setOnClickListener { save() }
    }

    private fun save() {
        val url = editUrl.text.toString().trim()
        if (url.isBlank()) {
            Toast.makeText(this, R.string.toast_save_first, Toast.LENGTH_SHORT).show()
            return
        }
        val token = editToken.text.toString().trim()
        Prefs.serverUrl = url
        Prefs.token = token
        Prefs.autostart = chkAutostart.isChecked
        Prefs.keepService = chkService.isChecked

        // 连通性校验
        lifecycleScope.launch {
            val ok = withContext(Dispatchers.IO) {
                try {
                    val resp = HarnessClient.create(url, token).health()
                    resp.isSuccessful && (resp.body()?.ok == true)
                } catch (e: Exception) {
                    false
                }
            }
            if (ok) {
                Toast.makeText(this@SettingsActivity, R.string.toast_connected, Toast.LENGTH_SHORT).show()
                if (Prefs.keepService) ConnectionService.start(this@SettingsActivity)
                // 返回主界面并加载
                finish()
            } else {
                Toast.makeText(
                    this@SettingsActivity,
                    getString(R.string.toast_connect_fail, "无法连接，请检查地址/网络"),
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }
}
