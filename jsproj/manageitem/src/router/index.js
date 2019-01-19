import Vue from 'vue'
import Router from 'vue-router'
import ItemManagement from '@/components/ItemManagement'
// import UploadPicture from '@/components/UploadPicture'

Vue.use(Router)

export default new Router({
  routes: [
    {
      path: '/',
      name: 'ItemManagement',
      component: ItemManagement
    }
  ]
})
